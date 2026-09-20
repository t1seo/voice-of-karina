import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never

import pytest

from tests.test_backends_api import ModelBoundary, write_fixture
from voice_of_karina.backends import synthesize, tts
from voice_of_karina.backends.protocol import AudioResponse, TranscriptResponse, WorkerTask
from voice_of_karina.contracts import GenerateRequest, Message, Provenance, Reference
from voice_of_karina.errors import VoiceError
from voice_of_karina.generation_settings import GenerationSettings

type Corruption = Literal[
    "none", "missing", "settings", "text", "sha", "model", "revision", "reference", "language"
]


@dataclass(frozen=True, slots=True)
class SamplingBoundary:
    corruption: Corruption = "none"

    def run(self, task: WorkerTask) -> AudioResponse | TranscriptResponse:
        response = ModelBoundary().run(task)
        assert isinstance(response, AudioResponse)
        artifact = response.audio[0]
        evidence = artifact.synthesis
        match self.corruption:
            case "none":
                return response
            case "missing":
                artifact = artifact.model_copy(update={"synthesis": None})
            case "settings":
                assert evidence is not None
                artifact = artifact.model_copy(
                    update={
                        "synthesis": evidence.model_copy(
                            update={"settings": GenerationSettings(seed=100)}
                        )
                    }
                )
            case "text":
                assert evidence is not None
                artifact = artifact.model_copy(
                    update={"synthesis": evidence.model_copy(update={"text": "다른 문장"})}
                )
            case "sha":
                assert evidence is not None
                artifact = artifact.model_copy(
                    update={"synthesis": evidence.model_copy(update={"audio_sha256": "0" * 64})}
                )
            case "model":
                artifact = artifact.model_copy(update={"model_id": "different/model"})
            case "revision":
                artifact = artifact.model_copy(update={"model_revision": "different-revision"})
            case "reference":
                artifact = artifact.model_copy(update={"reference": None})
            case "language":
                artifact = artifact.model_copy(update={"language": "English"})
            case unreachable:
                assert_never(unreachable)
        return AudioResponse(audio=(artifact,))


def prepared_reference(tmp_path: Path) -> Reference:
    path = tmp_path / "reference.wav"
    write_fixture(path)
    return Reference(
        audio_path=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        transcript="기준 음성이에요.",
        provenance=Provenance(kind="mimic"),
    )


def test_synthesis_preserves_explicit_recipe_through_worker_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    reference = prepared_reference(tmp_path)
    request = GenerateRequest(
        mode="reuse",
        messages=(Message(id="done", text="끝났어요."),),
        settings=GenerationSettings(seed=42, top_k=35),
    )
    monkeypatch.setattr(tts, "run_task", SamplingBoundary().run)

    # When
    result = synthesize(request, reference, tmp_path / "output")

    # Then
    assert result[0].synthesis is not None
    assert result[0].synthesis.settings == request.settings
    assert result[0].synthesis.text == request.messages[0].text


@pytest.mark.parametrize(
    "corruption",
    ["missing", "settings", "text", "sha", "model", "revision", "reference", "language"],
)
def test_synthesis_rejects_worker_artifact_that_does_not_match_requested_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: Corruption
) -> None:
    # Given
    reference = prepared_reference(tmp_path)
    request = GenerateRequest(
        mode="reuse",
        messages=(Message(id="done", text="끝났어요."),),
        settings=GenerationSettings(seed=42),
    )
    monkeypatch.setattr(tts, "run_task", SamplingBoundary(corruption).run)

    # When
    # Then
    with pytest.raises(VoiceError, match="synthesis evidence"):
        _ = synthesize(request, reference, tmp_path / "output")
