from pathlib import Path

import pytest

from voice_of_karina.contracts import AnalysisResult, AnalyzeRequest, Candidate
from voice_of_karina.storage import Store
from voice_of_karina.workflow import Workflow
from voice_of_karina.workflow_models import Adapters, JobResult


def test_analysis_when_legacy_candidate_has_no_digest_refreshes_with_same_job_id(
    tmp_path: Path,
) -> None:
    # Given
    store = Store(tmp_path)
    request = AnalyzeRequest(sources=("reference.wav",))
    job_id = store.request_id(request)
    candidate = Candidate(
        id="clip",
        source_id="source",
        audio_path="reference.wav",
        start_seconds=0,
        end_seconds=5,
        transcript="hello",
        needs_confirmation=False,
    )
    _ = store.save(
        JobResult(
            job_id=job_id,
            request=request,
            status="complete",
            analysis=AnalysisResult(candidates=(candidate,)),
        )
    )
    replacement = candidate.model_copy(update={"sha256": "1" * 64})
    adapters = Adapters(
        lambda _request, _directory: AnalysisResult(candidates=(replacement,)),
        lambda _candidate, _directory: pytest.fail(
            "Analysis must not prepare a generation reference"
        ),
        lambda _request, _directory: pytest.fail("Analysis must not design a voice"),
        lambda _request, _reference, _directory: pytest.fail(
            "Analysis must not spend a generation attempt"
        ),
        lambda _audio, _text, _policy: pytest.fail("Analysis must not validate generation"),
    )
    # When
    result = Workflow(store, adapters).analyze(request)
    # Then
    assert result.job_id == job_id
    assert result.status == "complete"
    assert result.analysis is not None
    assert result.analysis.candidates == (replacement,)
    assert result.messages == ()
