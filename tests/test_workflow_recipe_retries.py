from dataclasses import replace
from pathlib import Path

from tests.workflow_recipe_helpers import generation_request, recipe_adapters
from voice_of_karina.contracts import (
    GeneratedAudio,
    GenerateRequest,
    Message,
    QualityOptions,
    QualityResult,
    RejectRequest,
    ResumeRequest,
)
from voice_of_karina.generation_settings import GenerationSettings, settings_for_attempt
from voice_of_karina.quality_policy import QUALITY_POLICY_VERSION
from voice_of_karina.storage import Store
from voice_of_karina.workflow import Workflow
from voice_of_karina.workflow_state import accepted


def test_retry_when_first_line_fails_uses_distinct_stable_seed_and_preserves_sibling(
    tmp_path: Path,
) -> None:
    # Given
    store = Store(tmp_path)
    request = generation_request().model_copy(
        update={"messages": (Message(id="one", text="첫째"), Message(id="two", text="둘째"))}
    )
    calls: list[GenerateRequest] = []
    adapters = recipe_adapters(calls)

    def validate(audio: GeneratedAudio, _text: str, _options: QualityOptions) -> QualityResult:
        passed = audio.message_id == "one" or len(calls) == 3
        return QualityResult(
            valid=passed,
            decision="pass" if passed else "retry",
            policy_version=QUALITY_POLICY_VERSION,
        )

    # When
    result = Workflow(store, replace(adapters, validate=validate)).generate(request)
    # Then
    assert result.status == "complete"
    assert [item.attempts for item in result.messages] == [1, 2]
    assert [item.messages[0].id for item in calls] == ["one", "two", "two"]
    assert request.settings is not None
    assert calls[0].settings == calls[1].settings == request.settings
    assert calls[2].settings == settings_for_attempt(request.settings, "two", 2)
    assert calls[2].settings != calls[1].settings
    assert result.request == request


def test_resume_when_recipe_attempts_exhausted_never_replenishes_budget(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path)
    calls: list[GenerateRequest] = []
    adapters = recipe_adapters(calls)

    def validate(_audio: GeneratedAudio, _text: str, _policy: QualityOptions) -> QualityResult:
        return QualityResult(valid=False, decision="retry", policy_version=QUALITY_POLICY_VERSION)

    workflow = Workflow(store, replace(adapters, validate=validate))
    original = workflow.generate(generation_request())
    # When
    result = workflow.resume(ResumeRequest(job_id=original.job_id))
    # Then
    assert result.status == "failed"
    assert result.messages == original.messages
    assert len(calls) == 2
    assert len({call.settings.seed for call in calls if call.settings is not None}) == 2


def test_review_when_recipe_audio_rejected_retries_only_that_message(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path)
    calls: list[GenerateRequest] = []
    workflow = Workflow(store, recipe_adapters(calls))
    request = generation_request().model_copy(
        update={"messages": (Message(id="one", text="첫째"), Message(id="two", text="둘째"))}
    )
    original = workflow.generate(request)
    digest = original.messages[0].accepted_sha256
    assert digest is not None
    _ = workflow.reject(
        RejectRequest(
            job_id=original.job_id, message_id="one", sha256=digest, reason="Unclear onset"
        )
    )
    # When
    result = workflow.resume(ResumeRequest(job_id=original.job_id))
    # Then
    assert result.status == "complete"
    assert result.messages[0].attempts == 2
    assert result.messages[1] == original.messages[1]
    assert result.messages[0].accepted_sha256 != digest
    assert [item.messages[0].id for item in calls] == ["one", "two", "one"]


def test_recovery_when_rejected_recipe_artifact_is_only_remaining_candidate_stays_rejected(
    tmp_path: Path,
) -> None:
    # Given
    store = Store(tmp_path)
    calls: list[GenerateRequest] = []
    workflow = Workflow(store, recipe_adapters(calls))
    request = generation_request().model_copy(update={"max_attempts": 1})
    original = workflow.generate(request)
    digest = original.messages[0].accepted_sha256
    assert digest is not None
    rejected = workflow.reject(
        RejectRequest(
            job_id=original.job_id, message_id="done", sha256=digest, reason="Unclear onset"
        )
    )
    _ = store.save(
        rejected.model_copy(
            update={
                "messages": (
                    rejected.messages[0].model_copy(update={"audio": None, "quality": None}),
                )
            }
        )
    )
    # When
    result = workflow.resume(ResumeRequest(job_id=original.job_id))
    # Then
    assert result.status == "failed"
    assert not accepted(result.messages[0])
    assert result.messages[0].attempts == 1
    assert len(calls) == 1


def test_duplicate_when_same_recipe_request_is_submitted_reuses_verified_artifact(
    tmp_path: Path,
) -> None:
    # Given
    store = Store(tmp_path)
    calls: list[GenerateRequest] = []
    workflow = Workflow(store, recipe_adapters(calls))
    request = generation_request().model_copy(update={"settings": GenerationSettings(seed=71)})
    original = workflow.generate(request)
    # When
    repeated = workflow.generate(request)
    # Then
    assert repeated.job_id == original.job_id
    assert repeated.messages == original.messages
    assert len(calls) == 1
