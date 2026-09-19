from __future__ import annotations

import platform
import subprocess
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypedDict, Unpack

import pytest

from voice_of_karina.backends import client, runtime
from voice_of_karina.backends.protocol import (
    TASK_ADAPTER,
    CloneTask,
    FailureResponse,
    TranscribeTask,
    Transcript,
    TranscriptResponse,
    WorkerTask,
)
from voice_of_karina.contracts import Message, Provenance, Reference
from voice_of_karina.errors import VoiceError

if TYPE_CHECKING:
    from pathlib import Path


class RunOptions(TypedDict):
    input: str
    capture_output: bool
    text: bool
    check: bool
    timeout: int


@dataclass(frozen=True, slots=True)
class Invocation:
    command: tuple[str, ...]
    task: WorkerTask
    timeout: int


@dataclass(frozen=True, slots=True)
class ProcessBoundary:
    stdout: str
    returncode: int = 0
    calls: list[Invocation] = field(default_factory=list)
    timeout: bool = False

    def run(
        self,
        command: list[str],
        **options: Unpack[RunOptions],
    ) -> subprocess.CompletedProcess[str]:
        assert options["capture_output"]
        assert options["text"]
        assert not options["check"]
        self.calls.append(
            Invocation(
                tuple(command), TASK_ADAPTER.validate_json(options["input"]), options["timeout"]
            )
        )
        if self.timeout:
            raise subprocess.TimeoutExpired(command, options["timeout"])
        return subprocess.CompletedProcess(command, self.returncode, stdout=self.stdout, stderr="")


def available_runtime() -> None:
    return


def test_worker_preserves_json_text_and_pinned_deadline_when_serializing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    task = CloneTask(
        messages=(Message(id="quoted", text='"끝났어요."\n다시 확인해 주세요.'),),
        reference=Reference(
            audio_path=str(tmp_path / "ref.wav"),
            sha256="1" * 64,
            transcript="기준 문장이에요.",
            provenance=Provenance(kind="mimic"),
        ),
        output_dir=tmp_path,
        language="Korean",
    )
    boundary = ProcessBoundary(stdout=TranscriptResponse(transcripts=()).model_dump_json())
    monkeypatch.setattr(client, "require_local_runtime", available_runtime)
    monkeypatch.setattr(subprocess, "run", boundary.run)
    # When
    _ = client.run_task(task)
    # Then
    assert boundary.calls[0].task == task
    assert boundary.calls[0].command[1:] == ("-m", "voice_of_karina.backends.worker")
    assert boundary.calls[0].timeout == task.timeout_seconds


@pytest.mark.parametrize(
    ("stdout", "returncode", "expected_code"),
    [
        ("not JSON", 1, "invalid_model_response"),
        (
            FailureResponse(
                code="model_busy", message="Another model is running."
            ).model_dump_json(),
            1,
            "model_busy",
        ),
        (
            TranscriptResponse(transcripts=(Transcript(text="확인해 주세요."),)).model_dump_json(),
            -9,
            "model_execution_failed",
        ),
    ],
)
def test_worker_rejects_failed_process_when_reading_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    returncode: int,
    expected_code: str,
) -> None:
    # Given
    boundary = ProcessBoundary(stdout=stdout, returncode=returncode)
    monkeypatch.setattr(client, "require_local_runtime", available_runtime)
    monkeypatch.setattr(subprocess, "run", boundary.run)
    # When
    with pytest.raises(VoiceError) as failure:
        _ = client.run_task(TranscribeTask(paths=(tmp_path / "clip.wav",)))
    # Then
    assert failure.value.code == expected_code


@pytest.mark.skipif(sys.platform == "win32", reason="The local runtime uses Unix file locks.")
def test_model_lock_rejects_concurrent_work_when_deadline_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    # When
    with (
        runtime.inference_lock(timeout_seconds=0),
        pytest.raises(VoiceError) as failure,
        runtime.inference_lock(timeout_seconds=0),
    ):
        pytest.fail("A competing model was allowed to run.")
    # Then
    assert failure.value.code == "model_busy"


def test_runtime_rejects_unsupported_device_without_allocating_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setattr(sys, "platform", "linux")
    # When
    with pytest.raises(VoiceError) as failure:
        runtime.require_local_runtime()
    # Then
    assert failure.value.code == "unsupported_device"


def test_worker_returns_actionable_failure_when_model_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    boundary = ProcessBoundary(stdout="", timeout=True)
    monkeypatch.setattr(client, "require_local_runtime", available_runtime)
    monkeypatch.setattr(subprocess, "run", boundary.run)
    # When
    with pytest.raises(VoiceError) as failure:
        _ = client.run_task(TranscribeTask(paths=(tmp_path / "clip.wav",)))
    # Then
    assert failure.value.code == "model_timeout"


def test_runtime_rejects_older_macos_before_loading_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    monkeypatch.setattr(platform, "mac_ver", lambda: ("13.6", ("", "", ""), "arm64"))
    # When
    with pytest.raises(VoiceError) as failure:
        runtime.require_local_runtime()
    # Then
    assert failure.value.code == "unsupported_device"
