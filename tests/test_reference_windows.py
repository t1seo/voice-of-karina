from __future__ import annotations

import math
import random
from array import array

import pytest

from voice_of_karina import audio_reference_windows
from voice_of_karina.audio_io import AudioData
from voice_of_karina.audio_metrics import FRAME_SECONDS, SpeechFrame
from voice_of_karina.audio_reference_windows import utterance_reference_windows
from voice_of_karina.backends.stt import TranscriptSegment


def utterances() -> tuple[TranscriptSegment, ...]:
    return (
        TranscriptSegment(start=0.6, end=3.5, text="First utterance", avg_logprob=-0.1),
        TranscriptSegment(start=4.5, end=7.3, text="Second utterance", avg_logprob=-0.1),
    )


def quiet_frames() -> tuple[SpeechFrame, ...]:
    quiet = SpeechFrame(speech=False, rms=0.0001)
    speech = SpeechFrame(speech=True, rms=0.1)
    return (quiet,) * 20 + (speech,) * 97 + (quiet,) * 33 + (speech,) * 93 + (quiet,) * 24


def test_windows_cut_inside_measured_quiet_runs_outside_recognized_speech() -> None:
    # Given 30ms VAD frames with longer utterances and real low-energy pauses.
    frames = quiet_frames()
    parts = utterances()
    # When shorter windows are proposed.
    windows = utterance_reference_windows(frames, parts, 8.01)
    # Then both sides of each cut are quiet and the ASR speech has an outside margin.
    assert len(windows) == 2
    for window, part in zip(windows, parts, strict=True):
        assert 3 <= window.end - window.start <= 15
        assert 0.06 < part.start - window.start <= 0.45
        assert 0.06 < window.end - part.end <= 0.45
        for boundary in (window.start, window.end):
            index = round(boundary / FRAME_SECONDS)
            assert all(
                not frame.speech and frame.rms < 0.001 for frame in frames[index - 1 : index + 1]
            )


@pytest.mark.parametrize(
    "frames",
    [
        (SpeechFrame(speech=True, rms=0.001),) * 267,
        (SpeechFrame(speech=False, rms=0.1),) * 267,
        (SpeechFrame(speech=True, rms=0.1), SpeechFrame(speech=False, rms=0.0001)) * 134,
    ],
    ids=["quiet-voiced", "loud-nonspeech", "unstable-single-frame-pause"],
)
def test_windows_when_no_stable_low_energy_nonspeech_run_are_absent(
    frames: tuple[SpeechFrame, ...],
) -> None:
    # Given VAD decisions or energy levels that cannot establish a quiet cut.
    parts = utterances()
    # When possible shorter references are evaluated.
    windows = utterance_reference_windows(frames, parts, 8.01)
    # Then the original remains the only candidate.
    assert windows == ()


def test_windows_do_not_cut_activity_continuing_after_the_asr_end() -> None:
    # Given an early ASR endpoint followed by actual activity through the search range.
    frames = list(quiet_frames())
    frames[116:136] = [SpeechFrame(speech=False, rms=0.1)] * 20
    # When shorter references are evaluated.
    windows = utterance_reference_windows(tuple(frames), utterances(), 8.01)
    # Then the first utterance with the unsafe endpoint cannot become a candidate.
    assert [window.text for window in windows] == ["Second utterance"]


@pytest.mark.parametrize(
    "parts",
    [
        (TranscriptSegment(start=0.6, end=0.6, text="First"), utterances()[1]),
        (TranscriptSegment(start=3.5, end=0.6, text="First"), utterances()[1]),
        (utterances()[0], TranscriptSegment(start=4.5, end=8.1, text="Second")),
        (utterances()[0], TranscriptSegment(start=3.4, end=7.3, text="Second")),
        (utterances()[1], utterances()[0]),
        (utterances()[0], TranscriptSegment(start=4.5, end=7.3, text="  ... ")),
        (utterances()[0],),
    ],
    ids=["empty-interval", "reversed", "outside", "overlap", "out-of-order", "blank", "single"],
)
def test_windows_when_segments_are_invalid_do_not_refine(
    parts: tuple[TranscriptSegment, ...],
) -> None:
    # Given malformed, incomplete, or out-of-bounds ASR interval evidence.
    frames = quiet_frames()
    # When refinement is considered.
    windows = utterance_reference_windows(frames, parts, 8.01)
    # Then no interval is invented from the invalid segmentation.
    assert windows == ()


def test_windows_when_utterances_cannot_reach_three_seconds_are_absent() -> None:
    # Given quiet boundaries but two short recognized utterances.
    parts = (
        TranscriptSegment(start=0.6, end=1.1, text="First"),
        TranscriptSegment(start=2.1, end=2.6, text="Second"),
    )
    frames = (SpeechFrame(speech=False, rms=0),) * 110
    # When pause windows are bounded by the 0.45s search margin.
    windows = utterance_reference_windows(frames, parts, 3.3)
    # Then native audio is not padded to create a valid-length reference.
    assert windows == ()


@pytest.mark.parametrize("fricative", [False, True], ids=["voiced-onset", "fricative-like-onset"])
def test_fine_vad_preserves_quiet_pause_and_adjacent_speech_onset(fricative: bool) -> None:
    # Given a short pause between voiced audio and either voicing or broadband aspiration.
    generator = random.Random(23)  # noqa: S311 -- Reproducible acoustic noise fixture.
    samples: array[float] = array("f")
    for index in range(64000):
        time = index / 16000
        value = generator.gauss(0, 0.003)
        if time < 3 or time >= 3.18:
            value += (
                generator.gauss(0, 0.035)
                if fricative and time >= 3.18
                else 0.12 * math.sin(math.tau * (150 * time + 3 * math.sin(time * 5)))
            )
        samples.append(value)
    # When continuous fine-grained VAD decisions are aggregated for pause boundaries.
    frames = audio_reference_windows.pause_analysis_frames(AudioData(samples, 16000))
    # Then the stable quiet neighborhood ends before the next voiced or fricative-like onset.
    assert all(not frame.speech and frame.rms < 0.01 for frame in frames[104:106])
    assert all(frame.speech for frame in frames[106:109])
