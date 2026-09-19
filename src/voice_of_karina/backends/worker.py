"""Private JSON transport that releases all model memory when it exits."""

import sys
from contextlib import redirect_stdout
from typing import assert_never

from pydantic import ValidationError

from voice_of_karina.errors import VoiceError

from .native import execute
from .protocol import (
    TASK_ADAPTER,
    AudioResponse,
    FailureResponse,
    TranscriptResponse,
    WorkerResponse,
)
from .runtime import inference_lock, require_local_runtime


def main() -> int:
    """Keep stdout machine-readable even when model libraries print progress."""
    response: WorkerResponse
    try:
        task = TASK_ADAPTER.validate_json(sys.stdin.read())
    except ValidationError as error:
        response = FailureResponse(code="invalid_worker_request", message=str(error))
    else:
        try:
            require_local_runtime()
            with inference_lock(), redirect_stdout(sys.stderr):
                response = execute(task)
        except VoiceError as error:
            response = FailureResponse(code=error.code, message=error.message)
        except (ImportError, OSError, RuntimeError, ValueError, MemoryError) as error:
            response = FailureResponse(code="model_execution_failed", message=str(error))
    _ = sys.stdout.write(response.model_dump_json() + "\n")
    match response:
        case FailureResponse():
            return 1
        case AudioResponse() | TranscriptResponse():
            return 0
        case _:
            assert_never(response)


if __name__ == "__main__":
    raise SystemExit(main())
