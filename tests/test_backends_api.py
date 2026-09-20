from __future__ import annotations

import hashlib
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import assert_never

import pytest

from voice_of_karina.backends import design_reference, stt, synthesize, tts
from voice_of_karina.backends.models import CLONE_MODEL, DESIGN_MODEL
from voice_of_karina.backends.protocol import (
    AudioResponse,
    CloneTask,
    DesignTask,
    TranscribeTask,
    Transcript,
    TranscriptResponse,
    TranscriptSegment,
    WorkerTask,
)
from voice_of_karina.contracts import (
    GeneratedAudio,
    GenerateRequest,
    Message,
    Provenance,
    Reference,
)
from voice_of_karina.errors import VoiceError
from voice_of_karina.generation_settings import SynthesisEvidence


def write_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24_000)
        audio.writeframes(array("h", [0, 2000, -2000] * 8000).tobytes())


@dataclass(frozen=True, slots=True)
class ModelBoundary:
    calls: list[WorkerTask] = field(default_factory=list)
    uncertain: bool = False
    confidence_available: bool = True

    def run(self, task: WorkerTask) -> AudioResponse | TranscriptResponse:
        self.calls.append(task)
        match task:
            case CloneTask():
                artifacts: list[GeneratedAudio] = []
                for message in task.messages:
                    path = task.output_dir / f"{message.id}.wav"
                    write_fixture(path)
                    artifacts.append(
                        GeneratedAudio(
                            message_id=message.id,
                            path=str(path),
                            sample_rate=24_000,
                            duration_seconds=1,
                            model_id=CLONE_MODEL.repository,
                            model_revision=CLONE_MODEL.revision,
                            elapsed_seconds=0.1,
                            language=task.language,
                            reference=task.reference,
                            synthesis=(
                                SynthesisEvidence(
                                    settings=task.settings,
                                    text=message.text,
                                    audio_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                    runtime="test runtime",
                                )
                                if task.settings is not None
                                else None
                            ),
                        )
                    )
                return AudioResponse(audio=tuple(artifacts))
            case DesignTask():
                write_fixture(task.output_path)
                return AudioResponse(
                    audio=(
                        GeneratedAudio(
                            message_id="reference",
                            path=str(task.output_path),
                            sample_rate=24_000,
                            duration_seconds=1,
                            model_id=DESIGN_MODEL.repository,
                            model_revision=DESIGN_MODEL.revision,
                            elapsed_seconds=0.1,
                        ),
                    )
                )
            case TranscribeTask():
                return TranscriptResponse(
                    transcripts=tuple(
                        Transcript(
                            text="측정된 기준 음성이에요.",
                            language="ko",
                            segments=(
                                TranscriptSegment(
                                    start=0,
                                    end=1,
                                    text="측정된 기준 음성이에요.",
                                    avg_logprob=-0.1 if self.confidence_available else None,
                                    no_speech_prob=(
                                        (0.9 if self.uncertain else 0.01)
                                        if self.confidence_available
                                        else None
                                    ),
                                ),
                            ),
                        )
                        for _path in task.paths
                    )
                )
            case _:
                assert_never(task)


def test_synthesis_uses_stored_reference_when_mode_is_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    path = tmp_path / "original.wav"
    write_fixture(path)
    reference = Reference(
        audio_path=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        transcript="원래 음성이에요.",
        provenance=Provenance(kind="mimic", source="original.wav"),
    )
    request = GenerateRequest(mode="reuse", messages=(Message(id="done", text="끝났어요."),))
    boundary = ModelBoundary()
    monkeypatch.setattr(tts, "run_task", boundary.run)
    monkeypatch.setattr(stt, "run_task", boundary.run)
    # When
    outputs = synthesize(request, reference, tmp_path / "output")
    # Then
    assert len(boundary.calls) == 1
    assert outputs[0].reference == reference
    assert Path(outputs[0].path).is_file()


def test_design_persists_measured_reference_when_creating_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    request = GenerateRequest(
        mode="design",
        voice_description="차분한 한국어 여성 목소리",
        messages=(Message(id="rest", text="쉬어 가세요."),),
    )
    boundary = ModelBoundary()
    monkeypatch.setattr(tts, "run_task", boundary.run)
    monkeypatch.setattr(stt, "run_task", boundary.run)
    # When
    reference = design_reference(request, tmp_path)
    # Then
    assert [task.operation for task in boundary.calls] == ["design", "transcribe"]
    assert reference.transcript == "측정된 기준 음성이에요."
    assert reference.provenance.description == request.voice_description
    assert reference.model_revision == DESIGN_MODEL.revision
    assert reference.sha256 == hashlib.sha256(Path(reference.audio_path).read_bytes()).hexdigest()
    saved = Reference.model_validate_json((tmp_path / "designed-reference.json").read_text())
    assert saved == reference


def test_design_reuses_reference_when_stage_is_resumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    request = GenerateRequest(
        mode="design",
        voice_description="차분한 목소리",
        messages=(Message(id="rest", text="쉬어요."),),
    )
    boundary = ModelBoundary()
    monkeypatch.setattr(tts, "run_task", boundary.run)
    monkeypatch.setattr(stt, "run_task", boundary.run)
    first = design_reference(request, tmp_path)
    boundary.calls.clear()
    # When
    resumed = design_reference(request, tmp_path)
    # Then
    assert resumed == first
    assert boundary.calls == []


@pytest.mark.parametrize(
    "boundary", [ModelBoundary(uncertain=True), ModelBoundary(confidence_available=False)]
)
def test_design_rejects_uncertain_transcript_when_preparing_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: ModelBoundary
) -> None:
    # Given
    request = GenerateRequest(
        mode="design",
        voice_description="차분한 목소리",
        messages=(Message(id="rest", text="쉬어요."),),
    )
    monkeypatch.setattr(tts, "run_task", boundary.run)
    monkeypatch.setattr(stt, "run_task", boundary.run)
    # When
    with pytest.raises(VoiceError, match="transcript"):
        _ = design_reference(request, tmp_path)
    # Then
    assert not (tmp_path / "designed-reference.json").exists()


def test_synthesis_rejects_changed_reference_before_invoking_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    path = tmp_path / "changed.wav"
    write_fixture(path)
    reference = Reference(
        audio_path=str(path),
        sha256="0" * 64,
        transcript="기준이에요.",
        provenance=Provenance(kind="mimic"),
    )
    request = GenerateRequest(mode="reuse", messages=(Message(id="done", text="끝났어요."),))
    boundary = ModelBoundary()
    monkeypatch.setattr(tts, "run_task", boundary.run)
    monkeypatch.setattr(stt, "run_task", boundary.run)
    # When
    with pytest.raises(VoiceError, match="changed"):
        _ = synthesize(request, reference, tmp_path / "out")
    # Then
    assert boundary.calls == []
