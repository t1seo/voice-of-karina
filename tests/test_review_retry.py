from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from tests.review_helpers import reject, resume, review_case
from voice_of_karina import engine
from voice_of_karina.workflow_models import JobResult
from voice_of_karina.workflow_state import accepted

if TYPE_CHECKING:
    import pytest


def test_review_rejection_is_persisted_before_retry_and_preserves_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = review_case(tmp_path, monkeypatch)
    result = reject(case)
    assert isinstance(result, JobResult)
    assert not accepted(result.messages[0])
    assert result.messages[0].attempts == 1
    assert result.messages[1] == case.state.messages[1]
    assert not case.generated
    assert result.messages[0].rejections[0].quality == case.state.messages[0].quality
    assert case.store.load(result.job_id) == result
    retried = resume(case)
    assert retried.status == "complete"
    assert retried.messages[0].attempts == 2
    assert case.generated == [("attention",)]
    assert retried.reference == case.state.reference
    assert retried.messages[1] == case.state.messages[1]
    original_audio = case.state.messages[0].audio
    assert original_audio is not None
    assert Path(original_audio.path).is_file()


def test_replayed_rejection_does_not_reject_new_result_or_spend_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = review_case(tmp_path, monkeypatch)
    _ = reject(case)
    retried = resume(case)
    replay = reject(case)
    assert replay == retried
    again = resume(case)
    assert again.messages == retried.messages
    assert case.generated == [("attention",)]


def test_stale_review_digest_is_rejected_without_state_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = review_case(tmp_path, monkeypatch)
    before = case.store.load(case.state.job_id)
    result = reject(case, "0" * 64)
    assert isinstance(result, engine.ErrorResult)
    assert result.errors[0].code == "stale_review"
    assert case.store.load(case.state.job_id) == before
    assert not case.generated


def test_rejected_artifact_cannot_return_via_recovery_or_install_after_budget_exhaustion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = review_case(tmp_path, monkeypatch, budget=1)
    rejected = reject(case)
    assert isinstance(rejected, JobResult)
    message = rejected.messages[0]
    _ = case.store.save(
        rejected.model_copy(
            update={
                "messages": (
                    message.model_copy(update={"audio": None, "quality": None}),
                    rejected.messages[1],
                )
            }
        )
    )
    recovered = resume(case)
    assert recovered.status == "partial"
    assert recovered.messages[0].attempts == 1
    assert not accepted(recovered.messages[0])
    assert not case.generated
    install = engine.run(
        json.dumps(
            {
                "action": "install",
                "job_id": case.state.job_id,
                "message_id": "attention",
                "target": "codex",
            }
        ),
        case.store.root,
    )
    assert isinstance(install, engine.ErrorResult)
    assert install.errors[0].code == "unverified_output"


def test_new_attempt_with_identical_rejected_bytes_cannot_pass_automatic_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = review_case(tmp_path, monkeypatch, repeat_original=True)
    _ = reject(case)
    result = resume(case)
    assert result.status == "partial"
    assert result.messages[0].attempts == 3
    assert not accepted(result.messages[0])
    assert case.generated == [("attention",), ("attention",)]
    assert result.messages[1] == case.state.messages[1]
    assert resume(case).messages == result.messages


def test_multiple_quality_reviews_never_reset_the_original_attempt_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = review_case(tmp_path, monkeypatch)
    digest = case.state.messages[0].accepted_sha256
    for expected_attempts in (2, 3):
        assert digest is not None
        _ = reject(case, digest)
        result = resume(case)
        assert result.messages[0].attempts == expected_attempts
        digest = result.messages[0].accepted_sha256
    assert digest is not None
    _ = reject(case, digest)
    exhausted = resume(case)
    assert exhausted.status == "partial"
    assert exhausted.messages[0].attempts == 3
    assert len(exhausted.messages[0].rejections) == 3
    assert not accepted(exhausted.messages[0])
    assert case.generated == [("attention",), ("attention",)]
