from __future__ import annotations

import struct
import wave
from typing import TYPE_CHECKING

import pytest

from voice_of_karina import quality
from voice_of_karina.backends.stt import Transcript, TranscriptSegment
from voice_of_karina.contracts import GeneratedAudio

if TYPE_CHECKING:
    from pathlib import Path


def artifact(path: Path) -> GeneratedAudio:
    return GeneratedAudio(
        message_id="test",
        path=str(path),
        sample_rate=16000,
        duration_seconds=1.0,
        model_id="fixture",
        elapsed_seconds=0,
    )


def write_samples(path: Path, value: int) -> Path:
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        samples = (
            round(value * min(1, (16000 - index) / 3200)) if 0 < abs(value) < 32767 else value
            for index in range(16000)
        )
        output.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))
    return path


def test_silent_wav_cannot_pass_on_metadata_alone(tmp_path: Path) -> None:
    # Given a real, decodable WAV with plausible metadata but zero samples.
    audio = artifact(write_samples(tmp_path / "silent.wav", 0))
    # When output quality is validated.
    result = quality.validate_output(audio, "작업이 끝났어요")
    # Then the artifact fails before spending time on ASR.
    assert result.decision == "retry"
    assert not result.valid
    assert "silent" in " ".join(result.warnings).lower()


def test_clipped_wav_requires_regeneration(tmp_path: Path) -> None:
    # Given a full-scale clipped recording.
    audio = artifact(write_samples(tmp_path / "clipped.wav", 32767))
    # When output quality is validated.
    result = quality.validate_output(audio, "작업이 끝났어요")
    # Then the clipping measurement prevents a successful result.
    assert result.decision == "retry"
    assert result.metrics.clipping_ratio == 1.0


def test_missing_output_cannot_pass_on_metadata_alone(tmp_path: Path) -> None:
    # Given metadata that names an output which was never written.
    audio = artifact(tmp_path / "missing.wav")
    # When output quality is validated.
    result = quality.validate_output(audio, "작업이 끝났어요")
    # Then no artifact is accepted.
    assert not result.valid
    assert result.decision == "retry"


@pytest.mark.parametrize(
    ("expected", "observed", "error"),
    [
        ("작업이 끝났어요. 확인해 주세요!", "작업이끝났어요 확인해주 세요", 0.0),
        ("123", "124", 1 / 3),
        ("안녕하세요", "안녕히가세요", 0.4),
        ("hello", "", 1.0),
    ],
)
def test_text_error_rate_ignores_spacing_but_retains_spoken_content(
    expected: str,
    observed: str,
    error: float,
) -> None:
    # Given Korean or numeric transcripts with meaningful and cosmetic differences.
    # When character error rate is computed.
    result = quality.text_error_rate(expected, observed)
    # Then punctuation and spaces do not cause false rejections.
    assert result == pytest.approx(error)


def test_wrong_spoken_content_cannot_pass(tmp_path: Path) -> None:
    # Given a physically audible fixture and a distinct recognized sentence.
    audio = artifact(write_samples(tmp_path / "signal.wav", 1000))
    # When the transcript comparison runs through the real file validator.
    result = quality.validate_output(
        audio, "작업이 끝났어요", transcriber=lambda _: "다른 문장입니다"
    )
    # Then the mismatch requests regeneration even though the file is playable.
    assert result.valid
    assert result.decision == "retry"


def test_empty_asr_is_explicitly_unverified(tmp_path: Path) -> None:
    # Given a physically audible fixture whose ASR result is empty.
    audio = artifact(write_samples(tmp_path / "signal.wav", 1000))
    # When the transcript comparison runs.
    result = quality.validate_output(audio, "작업이 끝났어요", transcriber=lambda _: "")
    # Then uncertainty is not recorded as a text match.
    assert result.decision == "needs_input"
    assert result.metrics.text_error_rate is None


def test_nonfinite_float_wav_is_rejected(tmp_path: Path) -> None:
    # Given a valid IEEE-float WAV container carrying NaN samples.
    path = tmp_path / "nan.wav"
    pcm = struct.pack("<f", float("nan")) * 16000
    fmt = struct.pack("<HHIIHH", 3, 1, 16000, 64000, 4, 32)
    _ = path.write_bytes(
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )
    # When actual samples are decoded.
    result = quality.validate_output(artifact(path), "작업이 끝났어요")
    # Then a container with plausible metadata cannot conceal invalid samples.
    assert not result.valid
    assert "nonfinite" in " ".join(result.warnings)


@pytest.mark.parametrize(
    "segments",
    [
        (),
        (TranscriptSegment(start=0, end=1, text="작업이 끝났어요", no_speech_prob=0.95),),
        (TranscriptSegment(start=0, end=1, text="작업이 끝났어요", avg_logprob=-2),),
    ],
)
def test_uncertain_matching_asr_never_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    segments: tuple[TranscriptSegment, ...],
) -> None:
    # Given a real fixture WAV and externally recognized text without reliable segment evidence.
    audio = artifact(write_samples(tmp_path / "signal.wav", 1000))

    def recognition(_path: Path, *, language: str | None = "ko") -> Transcript:
        return Transcript(text="작업이 끝났어요", language=language or "ko", segments=segments)

    monkeypatch.setattr(quality, "transcribe_audio", recognition)
    # When the default confidence-aware validator sees an exact string match.
    result = quality.validate_output(audio, "작업이 끝났어요")
    # Then uncertain ASR remains unverified instead of becoming a successful generation.
    assert result.decision == "needs_input"
    assert result.metrics.text_error_rate is None


@pytest.mark.parametrize(
    ("requested", "expected"), [("English", "en"), ("Korean", "ko"), ("auto", None)]
)
def test_output_validation_forwards_artifact_language(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requested: str,
    expected: str | None,
) -> None:
    # Given an audible file with explicit generation-language metadata.
    audio = artifact(write_samples(tmp_path / "signal.wav", 1000)).model_copy(
        update={"language": requested}
    )
    observed: list[str | None] = []

    def recognition(_path: Path, *, language: str | None = "ko") -> Transcript:
        observed.append(language)
        return Transcript(
            text="Hello there",
            language=language or "en",
            segments=(
                TranscriptSegment(
                    start=0, end=1, text="Hello there", avg_logprob=0.0, no_speech_prob=0.0
                ),
            ),
        )

    monkeypatch.setattr(quality, "transcribe_audio", recognition)
    # When the standard validator requests a transcript.
    result = quality.validate_output(audio, "Hello there")
    # Then it uses the artifact's language and keeps the normal text comparison.
    assert observed == [expected]
    assert result.decision == "pass"


def test_missing_segment_confidence_is_unverified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given matching recognized text whose segment omitted every confidence value.
    audio = artifact(write_samples(tmp_path / "signal.wav", 1000))

    def recognition(_path: Path, *, language: str | None = "ko") -> Transcript:
        return Transcript(
            text="작업이 끝났어요",
            language=language or "ko",
            segments=(TranscriptSegment(start=0, end=1, text="작업이 끝났어요"),),
        )

    monkeypatch.setattr(quality, "transcribe_audio", recognition)
    # When confidence-aware validation receives incomplete ASR evidence.
    result = quality.validate_output(audio, "작업이 끝났어요")
    # Then missing confidence remains explicit and cannot turn into a successful match.
    assert result.decision == "needs_input"
    assert "confidence" in " ".join(result.warnings).lower()


def test_mixed_segment_confidence_is_unverified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given matching text with one measured segment and one missing confidence evidence.
    audio = artifact(write_samples(tmp_path / "signal.wav", 1000))

    def recognition(_path: Path, *, language: str | None = "ko") -> Transcript:
        return Transcript(
            text="작업이 끝났어요",
            language=language or "ko",
            segments=(
                TranscriptSegment(
                    start=0, end=0.5, text="작업이", avg_logprob=0.0, no_speech_prob=0.0
                ),
                TranscriptSegment(start=0.5, end=1, text="끝났어요"),
            ),
        )

    monkeypatch.setattr(quality, "transcribe_audio", recognition)
    # When confidence-aware validation examines the complete utterance.
    result = quality.validate_output(audio, "작업이 끝났어요")
    # Then evidence from one segment cannot verify a different segment.
    assert result.decision == "needs_input"
    assert result.metrics.text_error_rate is None
    assert "confidence" in " ".join(result.warnings).lower()
