from __future__ import annotations

import struct
import wave
from typing import TYPE_CHECKING

import pytest

from voice_of_karina import audio, audio_transcription
from voice_of_karina.backends.stt import Transcript, TranscriptSegment
from voice_of_karina.contracts import AnalyzeRequest, Candidate, Metrics, Source, SourceTime

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("requested", "expected"), [("English", "en"), ("Korean", "ko"), ("auto", None)]
)
def test_analysis_forwards_requested_language_to_asr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requested: str,
    expected: str | None,
) -> None:
    # Given a real WAV and a fixed candidate interval, independent of speaker detection.
    path = tmp_path / "reference.wav"
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        output.writeframes(struct.pack("<h", 1000) * 48000 * 4)
    observed: list[str | None] = []

    def candidates(source: Source, _job_dir: Path, *, selected: bool) -> tuple[Candidate, ...]:
        return (
            Candidate(
                id="candidate-test",
                source_id=source.id,
                audio_path=source.audio_path,
                start_seconds=0,
                end_seconds=4,
                metrics=Metrics(duration_seconds=4),
                needs_confirmation=not selected,
            ),
        )

    def recognize(
        paths: tuple[Path, ...], *, language: str | None = "ko"
    ) -> tuple[Transcript, ...]:
        observed.append(language)
        return tuple(
            Transcript(
                text="Hello there",
                language=language or "en",
                segments=(
                    TranscriptSegment(
                        start=0, end=4, text="Hello there", avg_logprob=-0.1, no_speech_prob=0.01
                    ),
                ),
            )
            for _ in paths
        )

    monkeypatch.setattr(audio, "source_candidates", candidates)
    monkeypatch.setattr(audio_transcription, "transcribe_many", recognize)
    request = AnalyzeRequest(
        sources=(str(path),),
        language=requested,
        source_time=SourceTime(start_seconds=0, end_seconds=4),
    )
    # When the complete reference analysis path prepares and transcribes its candidate.
    result = audio.analyze_sources(request, tmp_path / "job")
    # Then native crops still receive the requested ASR language instead of a fixed Korean code.
    assert observed == [expected]
    assert result.candidates[0].transcript == "Hello there"
