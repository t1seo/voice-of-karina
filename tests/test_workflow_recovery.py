from pathlib import Path

import pytest

from voice_of_karina.contracts import (
    AnalysisResult,
    AnalyzeRequest,
    Candidate,
    GeneratedAudio,
    GenerateRequest,
    Message,
    Provenance,
    QualityResult,
    Reference,
    ResumeRequest,
    Status,
)
from voice_of_karina.quality_policy import QUALITY_POLICY_VERSION
from voice_of_karina.storage import Store, file_digest
from voice_of_karina.workflow import Workflow
from voice_of_karina.workflow_models import Adapters, JobResult, MessageResult


@pytest.mark.parametrize(
    ("quality", "expected_status"),
    [
        (
            QualityResult(valid=True, decision="pass", policy_version=QUALITY_POLICY_VERSION),
            "complete",
        ),
        (
            QualityResult(
                valid=False,
                decision="retry",
                policy_version=QUALITY_POLICY_VERSION,
                warnings=("Ending is incomplete.",),
            ),
            "failed",
        ),
        (QualityResult(valid=True, decision="pass"), "needs_input"),
    ],
    ids=("current-pass", "current-rejection", "unchecked-legacy-pass"),
)
def test_finished_child_artifact_when_parent_interrupted_is_checked_without_inference(
    tmp_path: Path,
    quality: QualityResult,
    expected_status: Status,
) -> None:
    # Given
    store = Store(tmp_path)
    request = GenerateRequest(
        mode="design",
        voice_description="calm",
        max_attempts=1,
        messages=(Message(id="done", text="hello"),),
    )
    job_id = store.request_id(request)
    directory = store.job_dir(job_id)
    attempt_dir = directory / "attempt-1"
    attempt_dir.mkdir(parents=True)
    reference_path = directory / "reference.wav"
    _ = reference_path.write_bytes(b"reference")
    reference = Reference(
        audio_path=str(reference_path),
        sha256=file_digest(reference_path),
        transcript="hello",
        provenance=Provenance(kind="design", description="calm"),
    )
    output = attempt_dir / "done.wav"
    _ = output.write_bytes(b"finished-child-output")
    audio = GeneratedAudio(
        message_id="done",
        path=str(output),
        sample_rate=24000,
        duration_seconds=1,
        model_id="boundary",
        elapsed_seconds=0,
    )
    _ = (attempt_dir / "done.audio.json").write_text(audio.model_dump_json())
    _ = store.save(
        JobResult(
            job_id=job_id,
            request=request,
            reference=reference,
            step="generating",
            messages=(MessageResult(message_id="done", text="hello", attempts=1),),
        )
    )
    adapters = Adapters(
        lambda _request, _directory: AnalysisResult(),
        lambda _candidate, _directory: reference,
        lambda _request, _directory: reference,
        lambda _request, _reference, _directory: pytest.fail(
            "The spent budget cannot run inference"
        ),
        lambda _audio, _text, _policy: quality,
    )
    # When
    result = Workflow(store, adapters).resume(ResumeRequest(job_id=job_id))
    # Then
    assert result.status == expected_status
    assert result.messages[0].attempts == 1
    assert result.messages[0].audio == audio
    assert result.messages[0].quality is not None
    assert result.messages[0].accepted_sha256 == (
        file_digest(output) if expected_status == "complete" else None
    )
    assert store.load(job_id).messages == result.messages


@pytest.mark.parametrize("policy_version", [QUALITY_POLICY_VERSION, None])
def test_status_when_accepted_artifact_deleted_is_truthful_and_read_only(
    tmp_path: Path, policy_version: str | None
) -> None:
    # Given
    store = Store(tmp_path)
    request = GenerateRequest(
        mode="design", voice_description="calm", messages=(Message(id="done", text="hello"),)
    )
    job_id = store.request_id(request)
    audio = GeneratedAudio(
        message_id="done",
        path=str(store.job_dir(job_id) / "missing.wav"),
        sample_rate=24000,
        duration_seconds=1,
        model_id="boundary",
        elapsed_seconds=0,
    )
    _ = store.save(
        JobResult(
            job_id=job_id,
            request=request,
            status="complete",
            messages=(
                MessageResult(
                    message_id="done",
                    text="hello",
                    attempts=1,
                    audio=audio,
                    quality=QualityResult(
                        valid=True, decision="pass", policy_version=policy_version
                    ),
                    accepted_sha256="expected",
                ),
            ),
        )
    )
    before = (store.job_dir(job_id) / "job.json").read_bytes()
    adapters = Adapters(
        lambda _request, _directory: pytest.fail("Status cannot analyze"),
        lambda _candidate, _directory: pytest.fail("Status cannot prepare"),
        lambda _request, _directory: pytest.fail("Status cannot design"),
        lambda _request, _reference, _directory: pytest.fail("Status cannot synthesize"),
        lambda _audio, _text, _policy: pytest.fail("Status cannot call ASR"),
    )
    # When
    result = Workflow(store, adapters).status(job_id)
    # Then
    assert result.status == "failed"
    assert result.messages[0].audio is None
    assert (store.job_dir(job_id) / "job.json").read_bytes() == before


def test_saved_generation_when_requested_message_missing_is_rejected() -> None:
    # Given
    request = GenerateRequest(
        mode="design", voice_description="calm", messages=(Message(id="done", text="hello"),)
    )
    # When / Then
    with pytest.raises(ValueError, match="saved_messages"):
        _ = JobResult(job_id="job-a", request=request, status="complete", messages=())


def test_analysis_when_asr_recovers_can_resume(tmp_path: Path) -> None:
    # Given
    candidate = Candidate(
        id="clip",
        source_id="source",
        audio_path="unused.wav",
        start_seconds=0,
        end_seconds=5,
        needs_confirmation=False,
    )
    results = [
        AnalysisResult(candidates=(candidate,), input_request="ASR unavailable"),
        AnalysisResult(candidates=(candidate.model_copy(update={"transcript": "hello"}),)),
    ]
    adapters = Adapters(
        lambda _request, _directory: results.pop(0),
        lambda _candidate, _directory: pytest.fail("Analysis cannot prepare"),
        lambda _request, _directory: pytest.fail("Analysis cannot design"),
        lambda _request, _reference, _directory: pytest.fail("Analysis cannot synthesize"),
        lambda _audio, _text, _policy: pytest.fail("Analysis cannot check outputs"),
    )
    workflow = Workflow(Store(tmp_path), adapters)
    first = workflow.analyze(AnalyzeRequest(sources=("unused.wav",)))
    assert first.status == "needs_input"
    # When
    second = workflow.resume(ResumeRequest(job_id=first.job_id))
    # Then
    assert second.status == "complete"
    assert not results
