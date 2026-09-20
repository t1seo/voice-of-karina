import hashlib
from pathlib import Path

import pytest

from voice_of_karina.backends.models import CLONE_MODEL, backend_cache_key
from voice_of_karina.contracts import GenerateRequest, Message, Provenance, Reference
from voice_of_karina.errors import VoiceError
from voice_of_karina.generation_settings import GenerationSettings, VoiceRecipe
from voice_of_karina.storage import Store, file_digest
from voice_of_karina.workflow_models import VoiceSelection


def test_legacy_request_identity_keeps_consumed_budget(tmp_path: Path) -> None:
    request = GenerateRequest(
        mode="reuse",
        voice_id="voice-test",
        messages=(Message(id="done", text="작업이 끝났어요. 확인해 주세요."),),
    )
    store = Store(tmp_path, cache_key=backend_cache_key())
    assert store.request_id(request) == "job-a442d5e377ca0d7aaf7946ca"
    explicit = request.model_copy(update={"settings": GenerationSettings()})
    assert store.request_id(explicit) != store.request_id(request)


def make_reference(tmp_path: Path) -> Reference:
    source = tmp_path / "source.wav"
    _ = source.write_bytes(b"unchanged native PCM")
    return Reference(
        audio_path=str(source),
        sha256=file_digest(source),
        transcript="오늘 함께해요",
        provenance=Provenance(kind="mimic", source="https://example.com/source"),
    )


def make_recipe() -> VoiceRecipe:
    return VoiceRecipe(
        settings=GenerationSettings(),
        model_id=CLONE_MODEL.repository,
        model_revision=CLONE_MODEL.revision,
    )


def test_profile_identity_is_legacy_compatible_and_recipe_specific(tmp_path: Path) -> None:
    reference = make_reference(tmp_path)
    store = Store(tmp_path / "store")
    legacy = store.save_voice("원래 음성", reference)
    expected = hashlib.sha256(
        reference.model_copy(update={"audio_path": ""}).model_dump_json().encode()
    ).hexdigest()[:24]
    assert legacy.id == "voice-" + expected
    recipe = make_recipe()
    selected = store.save_voice("선택한 음성", reference, recipe=recipe)
    assert selected.id != legacy.id
    assert store.voice(selected.id).recipe == recipe
    replay = store.save_voice("다른 이름", reference, recipe=recipe)
    assert replay == selected
    other = store.save_voice(
        "다른 설정",
        reference,
        recipe=recipe.model_copy(update={"settings": GenerationSettings(seed=55)}),
    )
    assert other.id not in (legacy.id, selected.id)


def test_selection_and_metadata_tampering_cannot_replace_a_recipe(tmp_path: Path) -> None:
    reference = make_reference(tmp_path)
    store = Store(tmp_path / "store")
    selection = VoiceSelection(audition_id="audition-test", choice_id="a" * 64, reason="검토")
    profile = store.save_voice("검토한 음성", reference, recipe=make_recipe(), selection=selection)
    assert store.voice(profile.id).selection == selection
    changed = profile.model_copy(
        update={"recipe": make_recipe().model_copy(update={"language": "English"})}
    )
    store.atomic_write(store.root / "voices" / profile.id / "profile.json", changed)
    with pytest.raises(VoiceError, match="identity"):
        _ = store.voice(profile.id)
