"""One JSON request in and one typed result out for both conversation agents."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, assert_never

from pydantic import ValidationError

from voice_of_karina.audio import analyze_sources, prepare_reference
from voice_of_karina.backends import backend_cache_key, design_reference, synthesize
from voice_of_karina.contracts import (
    REQUEST_ADAPTER,
    AnalyzeRequest,
    FrozenModel,
    GenerateRequest,
    InstallRequest,
    RejectRequest,
    Request,
    ResumeRequest,
    StatusRequest,
    VoicesRequest,
)
from voice_of_karina.errors import VoiceError
from voice_of_karina.notifications import (
    InstallResult,
    NotificationInstallError,
    install_notifications,
)
from voice_of_karina.quality import validate_output
from voice_of_karina.sources import validate_source
from voice_of_karina.storage import Store
from voice_of_karina.workflow import Workflow
from voice_of_karina.workflow_models import Adapters, ErrorDetail, JobResult, VoicesResult
from voice_of_karina.workflow_state import accepted


class ErrorResult(FrozenModel):
    """Boundary errors never pretend a job was created or completed."""

    status: Literal["failed"] = "failed"
    errors: tuple[ErrorDetail, ...]


type Response = JobResult | VoicesResult | InstallResult | ErrorResult


def run(raw: str | bytes, root: Path | None = None) -> Response:
    """Parse once, validate source locations, then dispatch to the durable workflow."""
    try:
        request = REQUEST_ADAPTER.validate_json(raw)
        match request:
            case AnalyzeRequest() | GenerateRequest():
                for source in request.sources:
                    validate_source(source)
            case (
                StatusRequest()
                | ResumeRequest()
                | RejectRequest()
                | VoicesRequest()
                | InstallRequest()
            ):
                pass
            case unreachable:
                assert_never(unreachable)
        workflow = Workflow(
            Store(root, cache_key=backend_cache_key()),
            Adapters(
                analyze_sources, prepare_reference, design_reference, synthesize, validate_output
            ),
        )
        return _dispatch(request, workflow)
    except ValidationError as error:
        return ErrorResult(errors=(ErrorDetail(code="invalid_request", message=str(error)),))
    except VoiceError as error:
        return ErrorResult(errors=(ErrorDetail(code=error.code, message=error.message),))
    except NotificationInstallError as error:
        return ErrorResult(errors=(ErrorDetail(code="installation_failed", message=error.reason),))
    except OSError as error:
        return ErrorResult(errors=(ErrorDetail(code="io_error", message=str(error)),))


def _dispatch(request: Request, workflow: Workflow) -> Response:
    response: Response
    match request:
        case AnalyzeRequest():
            response = workflow.analyze(request)
        case GenerateRequest():
            response = workflow.generate(request)
        case StatusRequest():
            response = workflow.status(request.job_id)
        case ResumeRequest():
            response = workflow.resume(request)
        case RejectRequest():
            response = workflow.reject(request)
        case VoicesRequest():
            response = workflow.voices()
        case InstallRequest():
            response = apply_notification(request, workflow)
        case unreachable:
            assert_never(unreachable)
    return response


def apply_notification(request: InstallRequest, workflow: Workflow) -> InstallResult:
    """Installation requires an existing validated utterance from this exact job."""
    _ = workflow.store.load(request.job_id)
    with workflow.store.lock(request.job_id):
        state = workflow.status(request.job_id)
        result = next(
            (result for result in state.messages if result.message_id == request.message_id), None
        )
        if result is None or not accepted(result) or result.audio is None:
            raise VoiceError(
                "unverified_output", "Only a successfully verified message can be installed."
            )
        target = request.target
        tools: tuple[Literal["claude", "codex"], ...]
        match target:
            case "claude":
                tools = ("claude",)
            case "codex":
                tools = ("codex",)
            case "both":
                tools = ("claude", "codex")
            case unreachable:
                assert_never(unreachable)
        return install_notifications(Path(result.audio.path), tools)
