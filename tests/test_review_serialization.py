from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import TYPE_CHECKING

from tests.review_helpers import reject, resume, review_case
from voice_of_karina import engine, notifications
from voice_of_karina.storage import file_digest
from voice_of_karina.workflow_models import JobResult

if TYPE_CHECKING:
    import pytest

    from voice_of_karina.notifications import InstallResult, NotificationTool


def test_install_serializes_review_and_resume_through_the_settings_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = review_case(tmp_path / "state", monkeypatch)
    current = case.state.messages[0]
    assert current.audio is not None
    audio_path = Path(current.audio.path)
    with wave.open(str(audio_path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x60\x09\xa0\xf6" * 12000)
    digest = file_digest(audio_path)
    updated = current.model_copy(update={"accepted_sha256": digest})
    _ = case.store.save(
        case.state.model_copy(update={"messages": (updated, case.state.messages[1])})
    )
    concurrent: list[engine.Response] = []

    def install(path: Path, tools: tuple[NotificationTool, ...]) -> InstallResult:
        concurrent.append(reject(case, digest))
        concurrent.append(
            engine.run(
                json.dumps({"action": "resume", "job_id": case.state.job_id}), case.store.root
            )
        )
        return notifications.install_notifications(
            path, tools, codex_dir=tmp_path / "codex", claude_dir=tmp_path / "claude"
        )

    monkeypatch.setattr(engine, "install_notifications", install)
    request = json.dumps(
        {
            "action": "install",
            "job_id": case.state.job_id,
            "message_id": "attention",
            "target": "codex",
        }
    )
    result = engine.run(request, case.store.root)
    assert isinstance(result, notifications.InstallResult)
    assert len(concurrent) == 2
    for response in concurrent:
        assert isinstance(response, engine.ErrorResult)
        assert response.errors[0].code == "job_busy"
    installed = tmp_path / "codex" / "voice-of-karina" / "complete.wav"
    assert file_digest(installed) == digest
    assert not case.store.load(case.state.job_id).messages[0].rejections
    rejected = reject(case, digest)
    assert isinstance(rejected, JobResult)
    assert len(rejected.messages[0].rejections) == 1
    blocked = engine.run(request, case.store.root)
    assert isinstance(blocked, engine.ErrorResult)
    assert blocked.errors[0].code == "unverified_output"
    assert len(concurrent) == 2


def test_unknown_install_does_not_create_a_job_directory(tmp_path: Path) -> None:
    result = engine.run(
        '{"action":"install","job_id":"missing","message_id":"done","target":"codex"}',
        tmp_path,
    )
    assert isinstance(result, engine.ErrorResult)
    assert result.errors[0].code == "unknown_job"
    assert not (tmp_path / "jobs").exists()


def test_mixed_attempt_counts_preserve_every_rejected_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = review_case(tmp_path, monkeypatch)
    sibling = case.state.messages[1].model_copy(update={"attempts": 3})
    _ = case.store.save(
        case.state.model_copy(update={"messages": (case.state.messages[0], sibling)})
    )
    (case.store.job_dir(case.state.job_id) / "attempt-3").mkdir()
    digest = case.state.messages[0].accepted_sha256
    directories: list[Path] = []
    for expected_attempts in (2, 3):
        assert digest is not None
        _ = reject(case, digest)
        result = resume(case)
        message = result.messages[0]
        assert result.status == "complete"
        assert message.attempts == expected_attempts
        assert message.audio is not None
        directories.append(Path(message.audio.path).parent)
        assert result.messages[1] == sibling
        for rejection in message.rejections:
            assert file_digest(Path(rejection.audio.path)) == rejection.sha256
        digest = message.accepted_sha256
    assert len(set(directories)) == 2
    assert directories[0].name == "attempt-4"
    assert directories[1].name == "attempt-5"
    assert case.generated == [("attention",), ("attention",)]


def test_recovery_prefers_latest_numeric_batch_without_another_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = review_case(tmp_path, monkeypatch)
    _ = reject(case)
    original = case.state.messages[0].audio
    assert original is not None
    latest: Path | None = None
    for batch in (9, 10):
        directory = case.store.job_dir(case.state.job_id) / f"attempt-{batch}"
        directory.mkdir()
        latest = directory / "attention.wav"
        _ = latest.write_bytes(f"generation-{batch}".encode())
        audio = original.model_copy(update={"path": str(latest)})
        _ = latest.with_suffix(".audio.json").write_text(audio.model_dump_json())
    recovered = resume(case)
    assert recovered.status == "complete"
    assert recovered.messages[0].audio is not None
    assert Path(recovered.messages[0].audio.path) == latest
    assert recovered.messages[0].attempts == 1
    assert not case.generated
