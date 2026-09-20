from pathlib import Path

import pytest

from voice_of_karina.backends.models import CLONE_MODEL
from voice_of_karina.contracts import (
    GeneratedAudio,
    GenerateRequest,
    Message,
    Provenance,
    QualityResult,
    Reference,
)
from voice_of_karina.generation_settings import (
    GenerationSettings,
    SynthesisEvidence,
    VoiceRecipe,
)
from voice_of_karina.quality_policy import QUALITY_POLICY_VERSION
from voice_of_karina.storage import file_digest
from voice_of_karina.workflow_models import Adapters


def reference_file(directory: Path) -> Reference:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "reference.wav"
    _ = path.write_bytes(b"reference")
    return Reference(
        audio_path=str(path),
        sha256=file_digest(path),
        transcript="original reference",
        provenance=Provenance(kind="mimic", source="local source.wav"),
    )


def recipe(settings: GenerationSettings | None = None) -> VoiceRecipe:
    return VoiceRecipe(
        settings=settings or GenerationSettings(),
        model_id=CLONE_MODEL.repository,
        model_revision=CLONE_MODEL.revision,
        language="Korean",
    )


def generated_audio(
    request: GenerateRequest, reference: Reference | None, directory: Path
) -> list[GeneratedAudio]:
    outputs: list[GeneratedAudio] = []
    for message in request.messages:
        path = directory / f"{message.id}.wav"
        content = f"{directory.name}:{message.id}:{request.settings}".encode()
        _ = path.write_bytes(content)
        evidence = (
            SynthesisEvidence(
                settings=request.settings,
                text=message.text,
                audio_sha256=file_digest(path),
                runtime="test boundary runtime",
            )
            if request.settings is not None
            else None
        )
        audio = GeneratedAudio(
            message_id=message.id,
            path=str(path),
            sample_rate=24000,
            duration_seconds=1,
            elapsed_seconds=0,
            model_id=CLONE_MODEL.repository,
            model_revision=CLONE_MODEL.revision,
            language=request.language,
            reference=reference,
            synthesis=evidence,
        )
        _ = path.with_suffix(".audio.json").write_text(audio.model_dump_json())
        outputs.append(audio)
    return outputs


def recipe_adapters(calls: list[GenerateRequest]) -> Adapters:
    def synthesize(
        request: GenerateRequest, reference: Reference | None, directory: Path
    ) -> list[GeneratedAudio]:
        calls.append(request)
        return generated_audio(request, reference, directory)

    return Adapters(
        lambda _request, _directory: pytest.fail("Recipe reuse must not analyze"),
        lambda _candidate, _directory: pytest.fail("Recipe reuse must not prepare"),
        lambda _request, directory: reference_file(directory),
        synthesize,
        lambda _audio, _text, _policy: QualityResult(
            valid=True, decision="pass", policy_version=QUALITY_POLICY_VERSION
        ),
    )


def generation_request() -> GenerateRequest:
    return GenerateRequest(
        mode="design",
        voice_description="a calm voice",
        settings=GenerationSettings(seed=719),
        language="ko",
        messages=(Message(id="done", text="작업이 끝났어요."),),
    )
