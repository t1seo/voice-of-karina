import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from voice_of_karina import engine
from voice_of_karina.contracts import (
    GeneratedAudio,
    GenerateRequest,
    InstallRequest,
    Message,
    Provenance,
    QualityOptions,
    QualityResult,
    Reference,
    ResumeRequest,
)
from voice_of_karina.errors import VoiceError
from voice_of_karina.quality_policy import QUALITY_POLICY_VERSION
from voice_of_karina.storage import Store, file_digest
from voice_of_karina.workflow import Workflow
from voice_of_karina.workflow_models import Adapters, JobResult, MessageResult
from voice_of_karina.workflow_state import accepted


@dataclass(frozen=True, slots=True)
class MigrationScenario:
    workflow: Workflow
    state: JobResult
    validated: list[str]
    generated: list[tuple[str, ...]]


def scenario(tmp_path: Path, *, attempts: int = 1, content: bytes = b"good") -> MigrationScenario:
    store = Store(tmp_path)
    request = GenerateRequest(
        mode="design",
        voice_description="calm",
        max_attempts=attempts,
        messages=(Message(id="old", text="hello"), Message(id="current", text="ready")),
    )
    job_id = store.request_id(request)
    directory = store.job_dir(job_id)
    directory.mkdir(parents=True)
    reference_path = directory / "reference.wav"
    _ = reference_path.write_bytes(b"reference")
    reference = Reference(
        audio_path=str(reference_path),
        sha256=file_digest(reference_path),
        transcript="reference",
        provenance=Provenance(kind="design", description="calm"),
    )
    messages: list[MessageResult] = []
    for message in request.messages:
        path = directory / f"{message.id}.wav"
        _ = path.write_bytes(content if message.id == "old" else b"good")
        messages.append(
            MessageResult(
                message_id=message.id,
                text=message.text,
                attempts=1,
                audio=GeneratedAudio(
                    message_id=message.id,
                    path=str(path),
                    sample_rate=24000,
                    duration_seconds=1,
                    model_id="fixture",
                    elapsed_seconds=0,
                ),
                quality=QualityResult(
                    valid=True,
                    decision="pass",
                    policy_version=QUALITY_POLICY_VERSION if message.id == "current" else None,
                ),
                accepted_sha256=file_digest(path),
            )
        )
    state = store.save(
        JobResult(
            job_id=job_id,
            request=request,
            status="complete",
            reference=reference,
            messages=tuple(messages),
        )
    )
    validated: list[str] = []
    generated: list[tuple[str, ...]] = []

    def validate(audio: GeneratedAudio, _text: str, _options: QualityOptions) -> QualityResult:
        validated.append(audio.message_id)
        valid = Path(audio.path).read_bytes() == b"good"
        return QualityResult(
            valid=valid,
            decision="pass" if valid else "retry",
            policy_version=QUALITY_POLICY_VERSION,
            warnings=() if valid else ("Ending is incomplete.",),
        )

    def synthesize(
        request: GenerateRequest, _reference: Reference | None, directory: Path
    ) -> list[GeneratedAudio]:
        generated.append(tuple(message.id for message in request.messages))
        outputs: list[GeneratedAudio] = []
        for message in request.messages:
            path = directory / f"{message.id}.wav"
            _ = path.write_bytes(b"good")
            outputs.append(
                GeneratedAudio(
                    message_id=message.id,
                    path=str(path),
                    sample_rate=24000,
                    duration_seconds=1,
                    model_id="fixture",
                    elapsed_seconds=0,
                )
            )
        return outputs

    adapters = Adapters(
        lambda _request, _directory: pytest.fail("Saved reference cannot be reanalyzed"),
        lambda _candidate, _directory: pytest.fail("Saved reference cannot be prepared"),
        lambda _request, _directory: pytest.fail("Saved reference cannot be redesigned"),
        synthesize,
        validate,
    )
    return MigrationScenario(Workflow(store, adapters), state, validated, generated)


def test_legacy_json_when_loaded_does_not_acquire_current_verification(tmp_path: Path) -> None:
    # Given
    old = scenario(tmp_path).state
    encoded = old.model_dump_json(exclude_none=True)
    # When
    decoded = JobResult.model_validate_json(encoded)
    # Then
    assert decoded.messages[0].quality is not None
    assert decoded.messages[0].quality.policy_version is None
    assert not accepted(decoded.messages[0])


def test_stale_status_when_read_preserves_audio_without_models_or_writes(tmp_path: Path) -> None:
    # Given
    case = scenario(tmp_path)
    saved = case.workflow.store.job_dir(case.state.job_id) / "job.json"
    before = saved.read_bytes()
    # When
    result = case.workflow.status(case.state.job_id)
    # Then
    assert result.status == "needs_input"
    assert result.step == "quality_revalidation"
    assert result.input_request is not None
    assert "resume" in result.input_request.lower()
    assert result.messages[0].audio == case.state.messages[0].audio
    assert not accepted(result.messages[0])
    assert saved.read_bytes() == before
    assert not case.validated
    assert not case.generated


def test_stale_audio_when_install_requested_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    case = scenario(tmp_path)

    def refuse_install(_path: Path, _tools: tuple[str, ...]) -> None:
        pytest.fail("Unverified audio must never reach installation")

    monkeypatch.setattr(engine, "install_notifications", refuse_install)
    request = InstallRequest(job_id=case.state.job_id, message_id="old", target="codex")
    # When / Then
    with pytest.raises(VoiceError, match="successfully verified"):
        _ = engine.apply_notification(request, case.workflow)


def test_stale_valid_audio_when_resumed_revalidates_without_spending_attempt(
    tmp_path: Path,
) -> None:
    # Given
    case = scenario(tmp_path)
    # When
    result = case.workflow.resume(ResumeRequest(job_id=case.state.job_id))
    # Then
    assert result.status == "complete"
    assert case.validated == ["old"]
    assert not case.generated
    assert result.messages[0].attempts == 1
    assert accepted(result.messages[0])
    assert result.messages[1] == case.state.messages[1]


def test_stale_failure_when_budget_exhausted_records_rejection_without_regeneration(
    tmp_path: Path,
) -> None:
    # Given
    case = scenario(tmp_path, content=b"cut")
    # When
    result = case.workflow.resume(ResumeRequest(job_id=case.state.job_id))
    # Then
    assert result.status == "partial"
    assert case.validated == ["old"]
    assert not case.generated
    assert result.messages[0].attempts == 1
    assert result.messages[0].audio == case.state.messages[0].audio
    assert result.messages[0].quality is not None
    assert result.messages[0].quality.decision == "retry"
    assert result.messages[0].quality.policy_version == QUALITY_POLICY_VERSION
    assert result.messages[0].accepted_sha256 is None
    assert result.input_request is not None
    assert "new request" in result.input_request.lower()


def test_stale_failure_when_budget_remains_regenerates_only_rejected_sibling(
    tmp_path: Path,
) -> None:
    # Given
    case = scenario(tmp_path, attempts=2, content=b"cut")
    # When
    result = case.workflow.resume(ResumeRequest(job_id=case.state.job_id))
    # Then
    assert result.status == "complete"
    assert case.validated == ["old", "old"]
    assert case.generated == [("old",)]
    assert result.messages[0].attempts == 2
    assert result.messages[1] == case.state.messages[1]


def test_legacy_status_when_stdin_requested_reports_unverified_audio(tmp_path: Path) -> None:
    # Given
    case = scenario(tmp_path)
    saved = case.workflow.store.job_dir(case.state.job_id) / "job.json"
    _ = saved.write_text(case.state.model_dump_json(exclude_none=True))
    before = saved.read_bytes()
    environment = {**os.environ, "VOICE_OF_KARINA_HOME": str(tmp_path)}
    # When
    completed = subprocess.run(
        [sys.executable, "-m", "voice_of_karina"],
        input=f'{{"action":"status","job_id":"{case.state.job_id}"}}',
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    # Then
    assert completed.returncode == 0, completed.stderr
    result = JobResult.model_validate_json(completed.stdout)
    assert result.status == "needs_input"
    assert result.step == "quality_revalidation"
    assert result.messages[0].audio == case.state.messages[0].audio
    assert not accepted(result.messages[0])
    assert saved.read_bytes() == before
