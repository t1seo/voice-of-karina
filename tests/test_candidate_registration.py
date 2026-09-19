from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.test_reference_registration import transcript, wav_reference
from voice_of_karina import reference_registration
from voice_of_karina.contracts import AnalysisResult, AnalyzeRequest, Candidate
from voice_of_karina.curation_contracts import RegisterCandidateRequest
from voice_of_karina.errors import VoiceError
from voice_of_karina.storage import Store
from voice_of_karina.workflow_models import JobResult

if TYPE_CHECKING:
    from voice_of_karina.backends.stt import Transcript


def saved_analysis(store: Store) -> JobResult:
    directory = store.job_dir("job-analysis")
    directory.mkdir(parents=True)
    reference = wav_reference(directory / "candidate.wav")
    candidate = Candidate(
        id="candidate-original",
        source_id="source-original",
        source=reference.provenance.source,
        audio_path=reference.audio_path,
        sha256=reference.sha256,
        transcript=reference.transcript,
        start_seconds=24.5,
        end_seconds=28,
        pause_bounded=True,
    )
    return store.save(
        JobResult(
            job_id="job-analysis",
            request=AnalyzeRequest(sources=("recording.wav",), language="English"),
            analysis=AnalysisResult(candidates=(candidate,)),
        )
    )


def test_candidate_registration_uses_saved_identity_transcript_and_analysis_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an analyzed native recording selected by its existing candidate identifier.
    store = Store(tmp_path)
    previous = saved_analysis(store)
    assert previous.analysis is not None
    candidate = previous.analysis.candidates[0]

    def recognize(path: Path, *, language: str | None = "ko") -> Transcript:
        assert path.is_file()
        assert language == "en"
        return transcript(candidate.transcript or "")

    monkeypatch.setattr(reference_registration, "transcribe_audio", recognize)
    # When the skill registers that selection without reconstructing reference JSON.
    result = reference_registration.register_candidate(
        RegisterCandidateRequest(
            analysis_job_id=previous.job_id, candidate_id=candidate.id, name="Saved selection"
        ),
        store,
    )
    # Then persisted PCM identity, source boundaries, and language remain attached.
    assert result.voice.reference.sha256 == candidate.sha256
    assert result.voice.reference.provenance.start_seconds == candidate.start_seconds
    assert result.voice.reference.provenance.end_seconds == candidate.end_seconds
    assert "pause_bounded" in result.voice.reference.preparation
    assert result.voice.recipe is not None
    assert result.voice.recipe.language == "English"


@pytest.mark.parametrize(
    ("problem", "code"),
    [
        ("unknown_job", "unknown_job"),
        ("missing_analysis", "missing_analysis"),
        ("unknown_candidate", "unknown_candidate"),
        ("unhashed", "unverified_candidate"),
        ("changed", "invalid_reference"),
    ],
)
def test_candidate_registration_rejects_missing_or_changed_analysis_evidence(
    tmp_path: Path, problem: str, code: str
) -> None:
    # Given unavailable analysis or a candidate whose transcript is no longer hash-bound.
    store = Store(tmp_path)
    previous = saved_analysis(store)
    assert previous.analysis is not None
    candidate = previous.analysis.candidates[0]
    if problem == "missing_analysis":
        _ = store.save(previous.model_copy(update={"analysis": None}))
    if problem == "unhashed":
        analysis = AnalysisResult(candidates=(candidate.model_copy(update={"sha256": None}),))
        _ = store.save(previous.model_copy(update={"analysis": analysis}))
    if problem == "changed":
        _ = Path(candidate.audio_path).write_bytes(b"changed recording")
    # When the skill submits the saved candidate identity.
    with pytest.raises(VoiceError) as raised:
        _ = reference_registration.register_candidate(
            RegisterCandidateRequest(
                analysis_job_id="job-missing" if problem == "unknown_job" else previous.job_id,
                candidate_id="candidate-missing"
                if problem == "unknown_candidate"
                else candidate.id,
                name="Invalid selection",
            ),
            store,
        )
    # Then no reusable voice is created from stale or missing evidence.
    assert raised.value.code == code
    assert store.voices() == ()
