from dataclasses import replace
from pathlib import Path
from typing import Literal, assert_never

import pytest

from tests.workflow_recipe_helpers import (
    generated_audio,
    generation_request,
    recipe_adapters,
)
from voice_of_karina.contracts import GeneratedAudio, GenerateRequest, Reference, ResumeRequest
from voice_of_karina.storage import Store
from voice_of_karina.workflow import Workflow
from voice_of_karina.workflow_models import JobResult

type Fault = Literal[
    "reference_sha",
    "reference_text",
    "text",
    "seed",
    "digest",
    "language",
    "model",
    "revision",
    "missing_evidence",
]


def unrelated_audio(audio: GeneratedAudio, fault: Fault) -> GeneratedAudio:
    evidence = audio.synthesis
    reference = audio.reference
    assert evidence is not None
    assert reference is not None
    updates: dict[str, str | Reference | None] = {}
    match fault:
        case "reference_sha":
            updates["reference"] = reference.model_copy(update={"sha256": "0" * 64})
        case "reference_text":
            updates["reference"] = reference.model_copy(update={"transcript": "other"})
        case "text":
            return audio.model_copy(
                update={"synthesis": evidence.model_copy(update={"text": "other"})}
            )
        case "seed":
            settings = evidence.settings.model_copy(update={"seed": evidence.settings.seed + 1})
            return audio.model_copy(
                update={"synthesis": evidence.model_copy(update={"settings": settings})}
            )
        case "digest":
            return audio.model_copy(
                update={"synthesis": evidence.model_copy(update={"audio_sha256": "0" * 64})}
            )
        case "language":
            updates["language"] = "English"
        case "model":
            updates["model_id"] = "other-model"
        case "revision":
            updates["model_revision"] = "other-revision"
        case "missing_evidence":
            updates["synthesis"] = None
        case unreachable:
            assert_never(unreachable)
    return audio.model_copy(update=updates)


def clear_results(state: JobResult) -> JobResult:
    return state.model_copy(
        update={
            "messages": tuple(
                message.model_copy(update={"audio": None, "quality": None, "accepted_sha256": None})
                for message in state.messages
            )
        }
    )


@pytest.mark.parametrize(
    "fault",
    [
        "reference_sha",
        "reference_text",
        "text",
        "seed",
        "digest",
        "language",
        "model",
        "revision",
        "missing_evidence",
    ],
)
def test_generation_when_output_identity_differs_is_never_accepted(
    tmp_path: Path, fault: Fault
) -> None:
    # Given
    store = Store(tmp_path)
    request = generation_request().model_copy(update={"max_attempts": 1})
    adapters = recipe_adapters([])

    def synthesize(
        effective: GenerateRequest, reference: Reference | None, directory: Path
    ) -> list[GeneratedAudio]:
        return [unrelated_audio(generated_audio(effective, reference, directory)[0], fault)]

    # When
    result = Workflow(store, replace(adapters, synthesize=synthesize)).generate(request)
    # Then
    assert result.status == "failed"
    assert result.messages[0].accepted_sha256 is None
    assert result.messages[0].attempts == 1


@pytest.mark.parametrize(
    "fault",
    [
        "reference_sha",
        "reference_text",
        "text",
        "seed",
        "digest",
        "language",
        "model",
        "revision",
        "missing_evidence",
    ],
)
def test_recovery_when_manifest_identity_differs_does_not_accept_or_spend_more(
    tmp_path: Path, fault: Fault
) -> None:
    # Given
    store = Store(tmp_path)
    calls: list[GenerateRequest] = []
    workflow = Workflow(store, recipe_adapters(calls))
    original = workflow.generate(generation_request().model_copy(update={"max_attempts": 1}))
    audio = original.messages[0].audio
    assert audio is not None
    _ = (
        Path(audio.path)
        .with_suffix(".audio.json")
        .write_text(unrelated_audio(audio, fault).model_dump_json())
    )
    _ = store.save(clear_results(original))
    # When
    result = workflow.resume(ResumeRequest(job_id=original.job_id))
    # Then
    assert result.status == "failed"
    assert result.messages[0].audio is None
    assert result.messages[0].attempts == 1
    assert len(calls) == 1


@pytest.mark.parametrize("after_audio", [False, True])
def test_resume_when_interrupted_before_or_after_artifact_honors_consumed_attempt(
    tmp_path: Path, after_audio: bool
) -> None:
    # Given
    store = Store(tmp_path)
    calls: list[GenerateRequest] = []
    request = generation_request()
    adapters = recipe_adapters(calls)

    def synthesize(
        effective: GenerateRequest, reference: Reference | None, directory: Path
    ) -> list[GeneratedAudio]:
        assert store.load(store.request_id(request)).messages[0].attempts == 1
        if after_audio:
            _ = generated_audio(effective, reference, directory)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _ = Workflow(store, replace(adapters, synthesize=synthesize)).generate(request)
    # When
    result = Workflow(store, adapters).resume(ResumeRequest(job_id=store.request_id(request)))
    # Then
    assert result.status == "complete"
    assert result.messages[0].attempts == (1 if after_audio else 2)
    assert len(calls) == (0 if after_audio else 1)


def test_recovery_when_manifest_has_no_recorded_invocation_is_refused(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path)
    calls: list[GenerateRequest] = []
    workflow = Workflow(store, recipe_adapters(calls))
    original = workflow.generate(generation_request().model_copy(update={"max_attempts": 1}))
    audio = original.messages[0].audio
    assert audio is not None
    Path(audio.path).with_name("invocation.json").unlink(missing_ok=True)
    _ = store.save(clear_results(original))
    # When
    result = workflow.resume(ResumeRequest(job_id=original.job_id))
    # Then
    assert result.status == "failed"
    assert result.messages[0].audio is None
    assert len(calls) == 1


def test_status_when_accepted_recipe_metadata_changes_is_read_only_and_truthful(
    tmp_path: Path,
) -> None:
    # Given
    store = Store(tmp_path)
    workflow = Workflow(store, recipe_adapters([]))
    original = workflow.generate(generation_request())
    audio = original.messages[0].audio
    assert audio is not None
    _ = store.save(
        original.model_copy(
            update={
                "messages": (
                    original.messages[0].model_copy(
                        update={"audio": unrelated_audio(audio, "seed")}
                    ),
                )
            }
        )
    )
    path = store.job_dir(original.job_id) / "job.json"
    before = path.read_bytes()
    # When
    result = workflow.status(original.job_id)
    # Then
    assert result.status == "failed"
    assert result.messages[0].accepted_sha256 is None
    assert path.read_bytes() == before


def test_recovery_when_trial_is_moved_to_an_unrecorded_batch_refuses_it(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path)
    calls: list[GenerateRequest] = []
    workflow = Workflow(store, recipe_adapters(calls))
    original = workflow.generate(generation_request().model_copy(update={"max_attempts": 1}))
    audio = original.messages[0].audio
    assert audio is not None
    original_path = Path(audio.path)
    relocated = original_path.parent.with_name("attempt-99")
    _ = original_path.parent.rename(relocated)
    moved_audio = audio.model_copy(update={"path": str(relocated / original_path.name)})
    _ = (relocated / "done.audio.json").write_text(moved_audio.model_dump_json())
    _ = store.save(clear_results(original))
    # When
    result = workflow.resume(ResumeRequest(job_id=original.job_id))
    # Then
    assert result.status == "failed"
    assert result.messages[0].audio is None
    assert len(calls) == 1
