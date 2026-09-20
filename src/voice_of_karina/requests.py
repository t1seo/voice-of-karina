"""One discriminated transport boundary for all supported skill actions."""

from typing import Annotated

from pydantic import Field, TypeAdapter

from voice_of_karina.contracts import (
    AnalyzeRequest,
    GenerateRequest,
    InstallRequest,
    RejectRequest,
    ResumeRequest,
    StatusRequest,
    VoicesRequest,
)
from voice_of_karina.curation_contracts import (
    AuditionRequest,
    PresetRequest,
    RegisterCandidateRequest,
    RegisterRequest,
    SelectVoiceRequest,
)

type Request = Annotated[
    AnalyzeRequest
    | GenerateRequest
    | StatusRequest
    | ResumeRequest
    | RejectRequest
    | VoicesRequest
    | InstallRequest
    | RegisterRequest
    | RegisterCandidateRequest
    | PresetRequest
    | AuditionRequest
    | SelectVoiceRequest,
    Field(discriminator="action"),
]
REQUEST_ADAPTER: TypeAdapter[Request] = TypeAdapter(Request)
