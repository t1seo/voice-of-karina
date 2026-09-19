from __future__ import annotations

import math
import struct
import wave
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from voice_of_karina import audio, audio_transcription
from voice_of_karina.backends.stt import Transcript, TranscriptSegment
from voice_of_karina.contracts import AnalyzeRequest, Candidate, Metrics, SourceTime
from voice_of_karina.errors import VoiceError

if TYPE_CHECKING:
    from pathlib import Path


def write_reference(path: Path, *, continuous: bool = False) -> Path:
    def sample(index: int) -> int:
        time = index / 48000
        active = continuous or 0.6 <= time < 3.5 or 4.5 <= time < 7.3
        return round(6000 * math.sin(math.tau * 220 * time)) if active else 0

    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        output.writeframes(b"".join(struct.pack("<h", sample(index)) for index in range(384000)))
    return path


def parent_candidate(path: Path, *, confirmation: bool = True) -> Candidate:
    return Candidate(
        id="candidate-parent",
        source_id="source-original",
        source="source-original.wav",
        audio_path=str(path),
        start_seconds=123.250000123,
        end_seconds=131.250000123,
        metrics=Metrics(duration_seconds=8, speech_ratio=0.8),
        needs_confirmation=confirmation,
    )


def segment(start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text, avg_logprob=-0.1, no_speech_prob=0.01)


def original_transcript() -> Transcript:
    return Transcript(
        text="첫 문장입니다. 다음 문장입니다.",
        segments=(segment(0.6, 3.5, "첫 문장입니다."), segment(4.5, 7.3, "다음 문장입니다.")),
    )


@dataclass(frozen=True, slots=True)
class RefinementProbe:
    parent: Candidate
    candidates: tuple[Candidate, ...]
    batches: tuple[tuple[Path, ...], ...]
    warning: str | None


def verified_refinement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, confirmation: bool = True
) -> RefinementProbe:
    tmp_path.mkdir(exist_ok=True)
    parent = parent_candidate(write_reference(tmp_path / "parent.wav"), confirmation=confirmation)
    calls: list[tuple[Path, ...]] = []

    def recognize(
        paths: tuple[Path, ...], *, language: str | None = "ko"
    ) -> tuple[Transcript, ...]:
        calls.append(paths)
        if len(calls) == 1:
            return (original_transcript(),)
        return tuple(
            Transcript(
                text=part.text.replace(" ", "") + "!",
                segments=(segment(0.2, 2.9, part.text),),
                language=language or "",
            )
            for part in original_transcript().segments
        )

    monkeypatch.setattr(audio_transcription, "transcribe_many", recognize)
    candidates, warning = audio_transcription.transcribe_candidates((parent,), tmp_path / "job")
    return RefinementProbe(parent, candidates, tuple(calls), warning)


def test_transcription_offers_independently_verified_short_utterances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a real native-rate recording with two speech regions separated by silence.
    # When reference analysis transcribes and verifies possible shorter references.
    result = verified_refinement(tmp_path, monkeypatch)
    # Then independently transcribed short utterances are offered with the original fallback.
    assert len(result.batches) == 2
    assert result.warning is None
    assert [candidate.pause_bounded for candidate in result.candidates] == [True, True, False]
    assert result.candidates[-1].id == result.parent.id


@pytest.mark.parametrize("confirmation", [True, False])
def test_verified_candidates_preserve_confirmation_and_express_analysis_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, confirmation: bool
) -> None:
    # Given the caller's original speaker-selection confidence.
    # When reliable utterance candidates are independently verified.
    result = verified_refinement(tmp_path, monkeypatch, confirmation=confirmation)
    # Then measured pauses do not become a speaker identity or naturalness claim.
    refined = result.candidates[:2]
    assert all(candidate.needs_confirmation == confirmation for candidate in refined)
    assert all(candidate.utterance_count == 1 for candidate in refined)
    assert all("analysis candidate" in candidate.warnings[-1] for candidate in refined)
    assert all("naturalness" in candidate.warnings[-1] for candidate in refined)


def test_prepared_reference_preserves_native_pcm_and_exact_absolute_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a verified pause crop whose source interval starts at an absolute offset.
    result = verified_refinement(tmp_path, monkeypatch)
    candidate = next(item for item in result.candidates if item.transcript == "첫문장입니다.!")
    # When that reference is selected and persisted.
    reference = audio.prepare_reference(candidate, tmp_path / "prepared")
    # Then the reference contains exactly the original 48kHz PCM interval.
    start = round((candidate.start_seconds - result.parent.start_seconds) * 48000)
    end = round((candidate.end_seconds - result.parent.start_seconds) * 48000)
    with wave.open(result.parent.audio_path, "rb") as original:
        original.setpos(start)
        expected = original.readframes(end - start)
    with wave.open(reference.audio_path, "rb") as prepared:
        assert prepared.getframerate() == 48000
        assert prepared.readframes(prepared.getnframes()) == expected
    assert candidate.start_seconds == result.parent.start_seconds + 0.48
    assert reference.provenance.start_seconds == candidate.start_seconds
    assert reference.provenance.end_seconds == candidate.end_seconds
    assert reference.provenance.source_id == result.parent.source_id
    assert reference.provenance.source == result.parent.source
    assert "pause_bounded" in reference.preparation


@pytest.mark.parametrize(
    ("mismatch", "uncertain", "unavailable"),
    [(True, False, False), (False, True, False), (False, False, True)],
    ids=["different-words", "uncertain-asr", "unavailable-asr"],
)
def test_refinement_when_verification_fails_retains_transcribed_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: bool,
    uncertain: bool,
    unavailable: bool,
) -> None:
    # Given an original transcript and a second ASR pass that cannot verify its crops.
    parent = parent_candidate(write_reference(tmp_path / "parent.wav"))
    batches: list[tuple[Path, ...]] = []

    def recognize(
        paths: tuple[Path, ...], *, language: str | None = "ko"
    ) -> tuple[Transcript, ...]:
        batches.append(paths)
        if len(batches) == 1:
            return (original_transcript(),)
        if unavailable:
            raise VoiceError("asr_unavailable", "The local worker is unavailable.")
        return tuple(
            Transcript(
                text="다른 내용입니다" if mismatch else part.text,
                language=language or "",
                segments=(
                    part.model_copy(
                        update={
                            "start": 0.2,
                            "end": 2.9,
                            "avg_logprob": -2.0 if uncertain else -0.1,
                        }
                    ),
                ),
            )
            for part in original_transcript().segments
        )

    monkeypatch.setattr(audio_transcription, "transcribe_many", recognize)
    # When the additional reference verification runs.
    candidates, warning = audio_transcription.transcribe_candidates((parent,), tmp_path / "job")
    # Then the original remains available with its reliable full transcript.
    assert [candidate.id for candidate in candidates] == [parent.id]
    assert candidates[0].transcript == original_transcript().text
    assert (warning is not None) == unavailable
    assert len(batches) == 2


def test_transcription_without_safe_pauses_keeps_original_without_extra_asr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given continuous high-energy native PCM despite two recognized segments.
    parent = parent_candidate(write_reference(tmp_path / "parent.wav", continuous=True))
    batches: list[tuple[Path, ...]] = []

    def recognize(
        paths: tuple[Path, ...], *, language: str | None = "ko"
    ) -> tuple[Transcript, ...]:
        batches.append(paths)
        return (original_transcript().model_copy(update={"language": language or ""}),)

    monkeypatch.setattr(audio_transcription, "transcribe_many", recognize)
    # When reference transcription considers refinement.
    candidates, warning = audio_transcription.transcribe_candidates((parent,), tmp_path / "job")
    # Then no unsafe crop is manufactured and there is no unnecessary second inference.
    assert [candidate.id for candidate in candidates] == [parent.id]
    assert len(batches) == 1
    assert warning is None


@pytest.mark.parametrize(
    ("text", "parts"),
    [(None, ()), ("", original_transcript().segments), ("Uncertain speech", ())],
)
def test_initial_asr_when_unavailable_or_uncertain_preserves_original_without_refinement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    text: str | None,
    parts: tuple[TranscriptSegment, ...],
) -> None:
    # Given a local source whose initial transcription cannot be trusted.
    parent = parent_candidate(write_reference(tmp_path / "parent.wav"))
    batches: list[tuple[Path, ...]] = []

    def recognize(
        paths: tuple[Path, ...], *, language: str | None = "ko"
    ) -> tuple[Transcript, ...]:
        batches.append(paths)
        if text is None:
            raise VoiceError("asr_unavailable", "The local worker is unavailable.")
        return (Transcript(text=text, segments=parts, language=language or ""),)

    monkeypatch.setattr(audio_transcription, "transcribe_many", recognize)
    # When candidate transcription runs.
    candidates, _ = audio_transcription.transcribe_candidates((parent,), tmp_path / "job")
    # Then no candidate is invented or sent to a second ASR pass.
    assert len(batches) == 1
    assert [candidate.id for candidate in candidates] == [parent.id]
    assert not candidates[0].transcript


def test_refinement_batches_and_final_results_are_bounded_with_original_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given four original candidates that each contain two eligible utterances.
    parent = parent_candidate(write_reference(tmp_path / "parent.wav"))
    parents = tuple(parent.model_copy(update={"id": f"candidate-{index}"}) for index in range(4))
    batches: list[tuple[Path, ...]] = []

    def recognize(
        paths: tuple[Path, ...], *, language: str | None = "ko"
    ) -> tuple[Transcript, ...]:
        batches.append(paths)
        if len(batches) == 1:
            return tuple(original_transcript() for _ in paths)
        return tuple(
            Transcript(
                text=original_transcript().segments[index % 2].text,
                language=language or "",
                segments=(segment(0.2, 2.9, "Verified utterance"),),
            )
            for index in range(len(paths))
        )

    monkeypatch.setattr(audio_transcription, "transcribe_many", recognize)
    # When one extra batch verifies the proposed utterances.
    candidates, _ = audio_transcription.transcribe_candidates(parents, tmp_path / "job")
    # Then at most four crops are transcribed, and four results retain an original fallback.
    assert [len(batch) for batch in batches] == [4, 4]
    assert [candidate.pause_bounded for candidate in candidates] == [True, True, True, False]


@pytest.mark.parametrize("unavailable", [False, True])
def test_source_analysis_retains_usable_recommendation_when_optional_asr_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unavailable: bool
) -> None:
    # Given a complete source-analysis request over native PCM with two utterances.
    path = write_reference(tmp_path / "source.wav")
    request = AnalyzeRequest(
        sources=(str(path),), source_time=SourceTime(start_seconds=0, end_seconds=8)
    )

    def recognize(
        paths: tuple[Path, ...], *, language: str | None = "ko"
    ) -> tuple[Transcript, ...]:
        if len(paths) == 1:
            return (original_transcript(),)
        if unavailable:
            raise VoiceError("asr_unavailable", "The local worker is unavailable.")
        return tuple(
            Transcript(
                text=part.text, language=language or "", segments=(segment(0.2, 2.9, part.text),)
            )
            for part in original_transcript().segments
        )

    monkeypatch.setattr(audio_transcription, "transcribe_many", recognize)
    # When acquisition, measured candidate analysis, and separate transcription run.
    result = audio.analyze_sources(request, tmp_path / "job")
    # Then optional ASR failure retains a usable recommendation without demanding extra input.
    assert len(result.candidates) == (1 if unavailable else 3)
    assert result.candidates[0].pause_bounded != unavailable
    assert result.recommended_candidate_id == result.candidates[0].id
    assert result.input_request is None


def test_refinement_ids_are_stable_across_job_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given the same source interval and measured quiet boundaries.
    # When independent analysis jobs verify the same utterances.
    results = tuple(verified_refinement(tmp_path / str(index), monkeypatch) for index in range(2))
    # Then output-directory choice does not change stable candidate identities.
    assert [item.id for item in results[0].candidates] == [
        item.id for item in results[1].candidates
    ]
