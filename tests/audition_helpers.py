from dataclasses import dataclass, field
from pathlib import Path

from tests.test_backends_api import write_fixture
from voice_of_karina.backends.models import CLONE_MODEL
from voice_of_karina.contracts import (
    AnalysisResult,
    AnalyzeRequest,
    Candidate,
    GeneratedAudio,
    GenerateRequest,
    Message,
    Provenance,
    QualityOptions,
    QualityResult,
    Reference,
)
from voice_of_karina.curation_contracts import AuditionRequest
from voice_of_karina.errors import VoiceError
from voice_of_karina.generation_settings import SynthesisEvidence
from voice_of_karina.quality_policy import QUALITY_POLICY_VERSION
from voice_of_karina.storage import Store, file_digest
from voice_of_karina.workflow_models import Adapters


def audition_request(store: Store, directory: Path, *, count: int = 2) -> AuditionRequest:
    voices: list[str] = []
    for index in range(count):
        path = directory / f"source-{index}.wav"
        write_fixture(path)
        reference = Reference(
            audio_path=str(path),
            sha256=file_digest(path),
            transcript=f"원본 {index} 문장입니다.",
            provenance=Provenance(kind="mimic", source=str(path)),
        )
        voices.append(store.save_voice(f"candidate {index}", reference).id)
    return AuditionRequest(
        voice_ids=tuple(voices),
        messages=(Message(id="attention", text="확인해 주세요."),),
    )


@dataclass(frozen=True, slots=True)
class AuditionBackend:
    calls: list[GenerateRequest] = field(default_factory=list)
    validations: list[str] = field(default_factory=list)
    unavailable: list[bool] = field(default_factory=lambda: [False])
    crash_at: int | None = None
    write_before_crash: bool = False
    rejected_texts: tuple[str, ...] = ()

    def synthesize(
        self, request: GenerateRequest, reference: Reference | None, output_dir: Path
    ) -> list[GeneratedAudio]:
        self.calls.append(request)
        if len(self.calls) == self.crash_at and not self.write_before_crash:
            raise KeyboardInterrupt
        assert request.settings is not None
        assert reference is not None
        assert len(request.messages) == 1
        message = request.messages[0]
        path = output_dir / f"{message.id}.wav"
        write_fixture(path)
        audio = GeneratedAudio(
            message_id=message.id,
            path=str(path),
            sample_rate=24000,
            duration_seconds=1,
            model_id=CLONE_MODEL.repository,
            model_revision=CLONE_MODEL.revision,
            elapsed_seconds=0.1,
            language=request.language,
            reference=reference,
            synthesis=SynthesisEvidence(
                settings=request.settings,
                text=message.text,
                audio_sha256=file_digest(path),
                runtime="test runtime",
            ),
        )
        _ = path.with_suffix(".audio.json").write_text(audio.model_dump_json())
        if len(self.calls) == self.crash_at:
            raise KeyboardInterrupt
        return [audio]

    def validate(
        self, _audio: GeneratedAudio, text: str, _options: QualityOptions
    ) -> QualityResult:
        self.validations.append(text)
        return QualityResult(
            valid=True,
            decision=(
                "needs_input"
                if self.unavailable[0]
                else "retry"
                if text in self.rejected_texts
                else "pass"
            ),
            transcript="" if self.unavailable[0] else text,
            policy_version=QUALITY_POLICY_VERSION,
        )

    def analyze(self, _request: AnalyzeRequest | GenerateRequest, _path: Path) -> AnalysisResult:
        raise VoiceError("unused", "Auditions use frozen profiles.")

    def prepare(self, _candidate: Candidate, _path: Path) -> Reference:
        raise VoiceError("unused", "Auditions use frozen profiles.")

    def design(self, _request: GenerateRequest, _path: Path) -> Reference:
        raise VoiceError("unused", "Auditions use frozen profiles.")

    def adapters(self) -> Adapters:
        return Adapters(self.analyze, self.prepare, self.design, self.synthesize, self.validate)
