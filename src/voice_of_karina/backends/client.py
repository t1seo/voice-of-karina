"""Typed subprocess calls with bounded model lifetime and actionable failures."""

import subprocess
import sys
from typing import assert_never

from pydantic import ValidationError

from voice_of_karina.errors import VoiceError

from .protocol import (
    RESPONSE_ADAPTER,
    AudioResponse,
    FailureResponse,
    TranscriptResponse,
    WorkerTask,
)
from .runtime import require_local_runtime


def run_task(task: WorkerTask) -> AudioResponse | TranscriptResponse:
    """Run one isolated model batch and reject malformed or failed worker output."""
    require_local_runtime()
    timeout = task.timeout_seconds
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "voice_of_karina.backends.worker"],
            input=task.model_dump_json(),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise VoiceError(
            "model_timeout",
            f"The {task.operation} model exceeded its {timeout}s time limit. Resume later.",
        ) from error
    except OSError as error:
        raise VoiceError("model_start_failed", str(error)) from error
    if completed.stderr:
        _ = sys.stderr.write(completed.stderr[-6000:])
    try:
        response = RESPONSE_ADAPTER.validate_json(completed.stdout)
    except ValidationError as error:
        raise VoiceError(
            "invalid_model_response",
            f"Model worker failed (exit {completed.returncode}). Free memory and resume the job.",
        ) from error
    match response:
        case FailureResponse():
            raise VoiceError(response.code, response.message)
        case AudioResponse() | TranscriptResponse():
            if completed.returncode != 0:
                raise VoiceError(
                    "model_execution_failed", f"Model worker exited {completed.returncode}."
                )
            return response
        case _:
            assert_never(response)
