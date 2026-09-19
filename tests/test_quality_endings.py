from __future__ import annotations

import math
import wave
from array import array
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from voice_of_karina import quality
from voice_of_karina.audio_endings import EndpointEvidence
from voice_of_karina.contracts import GeneratedAudio
from voice_of_karina.quality import validate_output
from voice_of_karina.quality_policy import QUALITY_POLICY_VERSION

if TYPE_CHECKING:
    from voice_of_karina.audio_io import AudioData


def audible(path: Path) -> GeneratedAudio:
    rate = 16000
    samples = array(
        "h",
        (
            round(4000 * math.sin(2 * math.pi * 200 * index / rate) * min(1, (rate - index) / 3200))
            for index in range(rate)
        ),
    )
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(samples.tobytes())
    return artifact(path)


def artifact(path: Path) -> GeneratedAudio:
    return GeneratedAudio(
        message_id="ending",
        path=str(path),
        sample_rate=16000,
        duration_seconds=1,
        model_id="fixture",
        elapsed_seconds=0,
    )


@pytest.mark.parametrize(
    ("expected", "observed"),
    [
        (
            "요청하신 모든 작업이 끝났어요. 결과를 확인해 주세요.",
            "요청하신 모든 작업이 끝났어요. 결과를 확인해 주",
        ),
        ("The requested task is now complete.", "The requested task is now complet"),
    ],
)
def test_missing_ending_cannot_hide_in_small_whole_text_error_rate(
    tmp_path: Path, expected: str, observed: str
) -> None:
    result = validate_output(
        audible(tmp_path / "speech.wav"), expected, transcriber=lambda _: observed
    )
    assert result.decision == "retry"
    assert result.metrics.ending_text_match is False


def test_actual_validation_stamps_current_policy(tmp_path: Path) -> None:
    result = validate_output(
        audible(tmp_path / "speech.wav"), "확인해 주세요!", transcriber=lambda _: "확인해주세요"
    )
    assert result.decision == "pass"
    assert result.policy_version == QUALITY_POLICY_VERSION
    assert result.metrics.ending_text_match is True


def test_unavailable_vowel_ending_evidence_cannot_be_replaced_by_asr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(_audio: AudioData) -> EndpointEvidence:
        return EndpointEvidence(None, None, abrupt=False, ends_active=False)

    monkeypatch.setattr(quality, "endpoint_evidence", unavailable)
    result = validate_output(
        audible(tmp_path / "speech.wav"), "확인해 주세요.", transcriber=lambda _: "확인해 주세요."
    )
    assert result.decision == "needs_input"
    assert result.metrics.text_error_rate is None


@pytest.mark.parametrize("padding_seconds", [0, 1])
@pytest.mark.parametrize("gain", [1.0, 0.2, 0.07])
def test_published_truncated_ending_cannot_pass_even_with_matching_asr(
    tmp_path: Path, padding_seconds: int, gain: float
) -> None:
    source = Path(__file__).parent / "fixtures" / "truncated-ko-done.wav"
    target = tmp_path / "truncated.wav"
    with wave.open(str(source), "rb") as original, wave.open(str(target), "wb") as output:
        output.setparams(original.getparams())
        samples = array("h")
        samples.frombytes(original.readframes(original.getnframes()))
        output.writeframes(array("h", (round(sample * gain) for sample in samples)).tobytes())
        output.writeframes(b"\0\0" * original.getframerate() * padding_seconds)
    result = validate_output(
        artifact(target),
        "작업이 끝났어요. 확인해 주세요.",
        transcriber=lambda _: "작업이 끝났어요. 확인해주세요.",
    )
    assert result.decision == "retry"
    assert result.metrics.terminal_decay_seconds is not None
    assert "ending" in " ".join(result.warnings).lower()
