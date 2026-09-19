import hashlib
from pathlib import Path

import pytest

from tests.test_backends_generation import CloneModel
from voice_of_karina.backends.generation import clone_batch
from voice_of_karina.backends.protocol import CloneTask
from voice_of_karina.contracts import GeneratedAudio, Message, Provenance, Reference
from voice_of_karina.errors import VoiceError
from voice_of_karina.generation_settings import GenerationSettings


def sampling_task(tmp_path: Path) -> CloneTask:
    return CloneTask(
        messages=(
            Message(id="first", text="끝났어요."),
            Message(id="second", text="확인해 주세요."),
        ),
        reference=Reference(
            audio_path=str(tmp_path / "reference.wav"),
            sha256="a" * 64,
            transcript="참고하는 문장이에요.",
            provenance=Provenance(kind="mimic", source="sample.wav"),
        ),
        output_dir=tmp_path,
        language="Korean",
        settings=GenerationSettings(seed=42, temperature=0.7, top_p=0.95, top_k=30),
    )


def test_explicit_sampling_sets_seed_before_each_message_and_records_applied_recipe(
    tmp_path: Path,
) -> None:
    # Given
    task = sampling_task(tmp_path)
    model = CloneModel()
    seed_calls: list[tuple[int, int]] = []

    def seed_rng(seed: int) -> None:
        seed_calls.append((seed, len(model.calls)))

    # When
    result = clone_batch(task, model, seed_rng=seed_rng, runtime="test runtime")

    # Then
    assert seed_calls == [(42, 0), (42, 1)]
    assert task.settings is not None
    for message, call, item in zip(task.messages, model.calls, result.audio, strict=True):
        assert call.temperature == task.settings.temperature
        assert call.top_p == task.settings.top_p
        assert call.top_k == task.settings.top_k
        assert call.repetition_penalty == task.settings.repetition_penalty
        assert item.synthesis is not None
        assert item.synthesis.settings == task.settings
        assert item.synthesis.text == message.text
        assert (
            item.synthesis.audio_sha256 == hashlib.sha256(Path(item.path).read_bytes()).hexdigest()
        )
        assert item.synthesis.runtime == "test runtime"
        manifest = Path(item.path).with_suffix(".audio.json")
        assert GeneratedAudio.model_validate_json(manifest.read_text()) == item


def test_explicit_sampling_refuses_uncontrolled_rng_before_inference(tmp_path: Path) -> None:
    # Given
    task = sampling_task(tmp_path)
    model = CloneModel()

    # When
    with pytest.raises(VoiceError, match="sampling context"):
        _ = clone_batch(task, model)

    # Then
    assert model.calls == []
    assert not (tmp_path / "first.wav").exists()


def test_legacy_sampling_does_not_change_model_defaults_or_seed_rng(tmp_path: Path) -> None:
    # Given
    task = sampling_task(tmp_path).model_copy(update={"settings": None})
    model = CloneModel()
    seed_calls: list[int] = []

    # When
    result = clone_batch(task, model, seed_rng=seed_calls.append, runtime="test runtime")

    # Then
    assert seed_calls == []
    assert all(call.repetition_penalty == 1.05 for call in model.calls)
    assert all(item.synthesis is None for item in result.audio)
