from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

from voice_of_karina import engine
from voice_of_karina.contracts import (
    GeneratedAudio,
    GenerateRequest,
    Message,
    Provenance,
    QualityOptions,
    QualityResult,
    Reference,
)
from voice_of_karina.quality_policy import QUALITY_POLICY_VERSION
from voice_of_karina.storage import Store, file_digest
from voice_of_karina.workflow_models import JobResult, MessageResult


@dataclass(frozen=True, slots=True)
class ReviewCase:
    store: Store
    state: JobResult
    generated: list[tuple[str, ...]]


def review_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    budget: int = 3,
    repeat_original: bool = False,
) -> ReviewCase:
    store = Store(tmp_path)
    request = GenerateRequest(
        mode="design",
        voice_description="calm",
        max_attempts=budget,
        messages=(
            Message(id="attention", text="확인이 필요해요."),
            Message(id="done", text="끝났어요."),
        ),
    )
    job_id = store.request_id(request)
    directory = store.job_dir(job_id)
    attempt_dir = directory / "attempt-1"
    attempt_dir.mkdir(parents=True)
    path = directory / "reference.wav"
    _ = path.write_bytes(b"reference")
    reference = Reference(
        audio_path=str(path),
        sha256=file_digest(path),
        transcript="reference",
        provenance=Provenance(kind="design", description="calm"),
    )
    messages: list[MessageResult] = []
    quality = QualityResult(valid=True, decision="pass", policy_version=QUALITY_POLICY_VERSION)
    for message in request.messages:
        output = attempt_dir / f"{message.id}.wav"
        _ = output.write_bytes(message.id.encode())
        audio = GeneratedAudio(
            message_id=message.id,
            path=str(output),
            sample_rate=24000,
            duration_seconds=1,
            model_id="fixture",
            elapsed_seconds=0,
        )
        _ = output.with_suffix(".audio.json").write_text(audio.model_dump_json())
        messages.append(
            MessageResult(
                message_id=message.id,
                text=message.text,
                attempts=1,
                audio=audio,
                quality=quality,
                accepted_sha256=file_digest(output),
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
    generated: list[tuple[str, ...]] = []

    def synthesize(
        request: GenerateRequest, reference: Reference | None, directory: Path
    ) -> list[GeneratedAudio]:
        generated.append(tuple(message.id for message in request.messages))
        outputs: list[GeneratedAudio] = []
        for message in request.messages:
            output = directory / f"{message.id}.wav"
            content = message.id.encode()
            _ = output.write_bytes(
                content if repeat_original else directory.name.encode() + content
            )
            audio = GeneratedAudio(
                message_id=message.id,
                path=str(output),
                sample_rate=24000,
                duration_seconds=1,
                model_id="fixture",
                elapsed_seconds=0,
                reference=reference,
            )
            _ = output.with_suffix(".audio.json").write_text(audio.model_dump_json())
            outputs.append(audio)
        return outputs

    def validate(_audio: GeneratedAudio, _text: str, _options: QualityOptions) -> QualityResult:
        return quality

    monkeypatch.setattr(engine, "synthesize", synthesize)
    monkeypatch.setattr(engine, "validate_output", validate)
    return ReviewCase(store, state, generated)


def reject(case: ReviewCase, sha256: str | None = None) -> engine.Response:
    return engine.run(
        json.dumps(
            {
                "action": "reject",
                "job_id": case.state.job_id,
                "message_id": "attention",
                "sha256": sha256 or case.state.messages[0].accepted_sha256,
                "reason": "The quality review found an unwanted onset.",
            }
        ),
        case.store.root,
    )


def resume(case: ReviewCase) -> JobResult:
    response = engine.run(
        json.dumps({"action": "resume", "job_id": case.state.job_id}), case.store.root
    )
    assert isinstance(response, JobResult)
    return response
