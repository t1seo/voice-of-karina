from __future__ import annotations

from array import array

import pytest

from voice_of_karina import audio_metrics
from voice_of_karina.audio_io import AudioData
from voice_of_karina.audio_metrics import SpeechFrame


@pytest.mark.parametrize(
    ("frames", "expected_speech_ratio", "expected_noise_floor"),
    [
        ((SpeechFrame(speech=True, rms=0.1),) * 3, 1.0, None),
        (
            (SpeechFrame(speech=True, rms=0.1),) * 2 + (SpeechFrame(speech=False, rms=0.01),),
            2 / 3,
            -40.0,
        ),
    ],
)
def test_noise_estimate_requires_observed_nonspeech(
    monkeypatch: pytest.MonkeyPatch,
    frames: tuple[SpeechFrame, ...],
    expected_speech_ratio: float,
    expected_noise_floor: float | None,
) -> None:
    # Given VAD decisions with or without a measurable background interval.
    signal = AudioData(array("f", [0.1] * 1440), sample_rate=16000)

    def detected_frames(_audio: AudioData) -> tuple[SpeechFrame, ...]:
        return frames

    monkeypatch.setattr(audio_metrics, "speech_frames", detected_frames)
    # When reference quality is measured.
    result = audio_metrics.reference_metrics(signal)
    # Then speech-only input does not receive an invented background estimate.
    assert result.speech_ratio == pytest.approx(expected_speech_ratio)
    if expected_noise_floor is None:
        assert result.noise_floor_dbfs is None
    else:
        assert result.noise_floor_dbfs == pytest.approx(expected_noise_floor)
