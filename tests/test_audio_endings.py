from __future__ import annotations

import math
from array import array
from pathlib import Path

import pytest

from voice_of_karina import audio_endings
from voice_of_karina.audio_io import AudioData, decode_audio
from voice_of_karina.errors import VoiceError


def framed_tone(levels: tuple[float | None, ...]) -> AudioData:
    samples = array(
        "f",
        (
            0.0
            if level is None
            else math.sqrt(2) * 10 ** (level / 20) * math.sin(2 * math.pi * index / 10)
            for level in levels
            for index in range(10)
        ),
    )
    return AudioData(samples, 1000)


def at_pcm16_gain(audio: AudioData, gain: float) -> AudioData:
    return AudioData(
        array("f", (round(sample * gain * 32768) / 32768 for sample in audio.samples)),
        audio.sample_rate,
    )


@pytest.mark.parametrize("gain", [1.0, 0.2, 0.07])
def test_truncated_open_vowel_stays_abrupt_when_silence_is_added(gain: float) -> None:
    # Given the original reported faulty WAV and a copy with one second of extra zeros.
    path = Path(__file__).parent / "fixtures" / "truncated-ko-done.wav"
    decoded = decode_audio(path)
    audio = at_pcm16_gain(decoded, gain)
    padded = AudioData(audio.samples + array("f", [0.0]) * audio.sample_rate, audio.sample_rate)
    # When the actual endpoint is measured independently of container duration.
    original, extended = (audio_endings.endpoint_evidence(item) for item in (audio, padded))
    # Then padding cannot hide the same abrupt physical ending.
    assert original == extended
    assert original.abrupt
    assert original.decay_seconds == pytest.approx(0.01)
    assert original.drop_db is not None
    assert original.drop_db >= 18
    assert not original.ends_active


@pytest.mark.parametrize("gain", [1.0, 0.2, 0.07])
def test_gently_decaying_tone_has_no_abrupt_ending(gain: float) -> None:
    # Given a tone whose last vowel-like amplitude decays over 200 ms.
    tone = framed_tone((-20.0,) * 50 + tuple(-20.0 - 2 * i for i in range(20)) + (None,) * 8)
    audio = at_pcm16_gain(tone, gain)
    # When its final high-to-low energy transition is measured.
    result = audio_endings.endpoint_evidence(audio)
    # Then a gradual natural-style decay is not mistaken for a cutoff.
    assert not result.abrupt
    assert not result.ends_active
    assert result.decay_seconds is not None
    assert result.decay_seconds >= 0.08


@pytest.mark.parametrize("level", [-20.0, -48.0, -65.0])
def test_cut_without_silence_exposes_active_file_boundary(level: float) -> None:
    # Given a tone that continues at the same volume until the file ends.
    audio = framed_tone((level,) * 50)
    # When no lower-energy frame exists to establish an observed drop.
    result = audio_endings.endpoint_evidence(audio)
    # Then the active boundary is explicit without inventing a silent frame.
    assert result.ends_active
    assert not result.abrupt
    assert result.drop_db is None


def test_cut_with_silence_has_a_measured_abrupt_drop() -> None:
    # Given the same sustained tone cut into genuine silence.
    audio = framed_tone((-20.0,) * 50 + (None,) * 10)
    # When its measured terminal transition is examined.
    result = audio_endings.endpoint_evidence(audio)
    # Then actual quiet samples establish an abrupt drop.
    assert result.abrupt
    assert not result.ends_active
    assert result.decay_seconds == 0
    assert result.drop_db is not None
    assert result.drop_db >= 18


def test_interior_pause_does_not_override_final_gentle_decay() -> None:
    # Given an early hard pause followed by a complete tone with a gentle ending.
    audio = framed_tone(
        (-20.0,) * 20
        + (None,) * 20
        + (-20.0,) * 30
        + tuple(-20.0 - 2 * i for i in range(20))
        + (None,) * 8
    )
    # When only the final high-to-low endpoint is measured.
    result = audio_endings.endpoint_evidence(audio)
    # Then an internal transition does not trigger an ending warning.
    assert not result.abrupt
    assert not result.ends_active


def test_partial_frame_is_not_zero_padded_into_a_fake_drop() -> None:
    # Given an active full frame followed by one sample at a sinusoid's zero crossing.
    full = framed_tone((-20.0,) * 50)
    audio = AudioData(full.samples + array("f", [0.0]), full.sample_rate)
    # When a partial frame cannot support a complete 10 ms RMS observation.
    result = audio_endings.endpoint_evidence(audio)
    # Then no fictional quiet frame is added by padding to the analysis grid.
    assert result.ends_active
    assert not result.abrupt
    assert result.drop_db is None


@pytest.mark.parametrize("levels", [(), (None,) * 10, (-125.0,) * 10, (-95.0,) * 10])
def test_empty_or_unmeasurable_audio_has_no_supported_endpoint(
    levels: tuple[float | None, ...],
) -> None:
    # Given empty, silent, or audio whose low threshold is below the measurement floor.
    audio = framed_tone(levels)
    # When terminal decay is measured without usable speech-level evidence.
    result = audio_endings.endpoint_evidence(audio)
    # Then missing evidence is explicit rather than a false clean endpoint.
    assert result.decay_seconds is None
    assert result.drop_db is None
    assert not result.abrupt
    assert not result.ends_active


def test_shorter_than_one_frame_has_no_supported_endpoint() -> None:
    # Given fewer samples than one complete analysis frame.
    audio = AudioData(array("f", [0.1] * 9), 1000)
    # When the endpoint evidence is requested.
    result = audio_endings.endpoint_evidence(audio)
    # Then the unobserved transition is not synthesized by zero padding.
    assert result.decay_seconds is None
    assert result.drop_db is None
    assert not result.abrupt
    assert not result.ends_active


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_samples_are_rejected_including_partial_tail(value: float) -> None:
    # Given an invalid PCM value inside an otherwise full-length tone.
    audio = AudioData(framed_tone((-20.0,) * 5).samples + array("f", [value]), 1000)
    # When acoustic evidence would otherwise silently discard the partial frame.
    with pytest.raises(VoiceError, match="nonfinite"):
        # Then invalid PCM cannot produce credible measurements.
        _ = audio_endings.endpoint_evidence(audio)


@pytest.mark.parametrize("sample_rate", [0, -1])
def test_nonpositive_sample_rate_is_rejected(sample_rate: int) -> None:
    # Given malformed audio metadata without a meaningful time scale.
    audio = AudioData(array("f", [0.1] * 100), sample_rate)
    # When the endpoint is measured.
    with pytest.raises(VoiceError, match="sample rate"):
        # Then a domain error identifies the invalid metadata.
        _ = audio_endings.endpoint_evidence(audio)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("작업이 끝났어요. 확인해 주세요.", True),
        ("  확인해 주세요! ”\n", True),
        ("\u110b\u116d.", True),
        ("확인!", False),
        ("끝.", False),
        ("Task complete.", False),
        ("안녕하세요 42.", False),
        ("확인해 주세요 😊", True),
        ("확인해 주세요 + ✨ ©", True),
        ("확인해 주세요🙂 42", False),
        ("Hello there 😊", False),
        ("", False),
        ("...", False),
    ],
)
def test_open_korean_vowel_hint_uses_only_final_spoken_character(text: str, expected: bool) -> None:
    # Given text in Korean or another language with punctuation and normalization variants.
    # When the final written Korean syllable is examined.
    result = audio_endings.requested_text_ends_with_korean_open_vowel(text)
    # Then only a composed Hangul syllable without a final consonant is identified.
    assert result is expected


@pytest.mark.parametrize(("decay_frames", "abrupt"), [(3, True), (4, False)])
def test_abrupt_decay_requires_strictly_less_than_forty_milliseconds(
    decay_frames: int, abrupt: bool
) -> None:
    # Given equal drops with controlled 30 ms or 40 ms high-to-low transitions.
    audio = framed_tone((-20.0,) * 50 + (-30.0,) + (-45.0,) * decay_frames + (-70.0,) * 10)
    # When the ending is examined at the explicit duration boundary.
    result = audio_endings.endpoint_evidence(audio)
    # Then the threshold includes only transitions shorter than 40 ms.
    assert result.decay_seconds == pytest.approx(decay_frames * 0.01)
    assert result.abrupt is abrupt


@pytest.mark.parametrize(("terminal_level", "abrupt"), [(-64.85, False), (-65.05, True)])
def test_abrupt_decay_requires_an_observed_eighteen_decibel_drop(
    terminal_level: float, abrupt: bool
) -> None:
    # Given short decays whose actual endpoint drop is just below or above 18 dB.
    audio = framed_tone((-35.0,) * 50 + (-46.95, -52.0) + (terminal_level,) * 10)
    # When a decrease smaller than the relative threshold span is not a complete decay.
    result = audio_endings.endpoint_evidence(audio)
    # Then the decision uses measured amplitude difference as well as elapsed time.
    if abrupt:
        assert result.drop_db == pytest.approx(-46.95 - terminal_level)
    else:
        assert result.drop_db is None
    assert result.abrupt is abrupt
