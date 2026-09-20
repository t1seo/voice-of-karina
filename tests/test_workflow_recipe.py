from pathlib import Path

import pytest

from tests.workflow_recipe_helpers import (
    generation_request,
    recipe,
    recipe_adapters,
    reference_file,
)
from voice_of_karina import engine
from voice_of_karina.contracts import GenerateRequest, Message, ResumeRequest
from voice_of_karina.generation_settings import GenerationSettings
from voice_of_karina.storage import Store
from voice_of_karina.workflow import Workflow
from voice_of_karina.workflow_models import JobResult


def test_reuse_when_profile_has_recipe_restores_sampling_and_canonical_language(
    tmp_path: Path,
) -> None:
    # Given
    store = Store(tmp_path)
    saved_recipe = recipe(GenerationSettings(seed=31, temperature=0.7))
    profile = store.save_voice("Chosen reference", reference_file(tmp_path), recipe=saved_recipe)
    request = GenerateRequest(
        mode="reuse",
        voice_id=profile.id,
        language="ko",
        messages=(Message(id="done", text="작업이 끝났어요."),),
    )
    calls: list[GenerateRequest] = []
    # When
    result = Workflow(store, recipe_adapters(calls)).generate(request)
    # Then
    assert result.status == "complete"
    assert result.recipe == saved_recipe
    assert len(calls) == 1
    assert calls[0].settings == saved_recipe.settings
    assert calls[0].language == "Korean"
    assert result.request == request
    assert isinstance(result.request, GenerateRequest)
    assert result.request.settings is None


def test_reuse_when_explicit_settings_override_profile_keeps_saved_profile_unchanged(
    tmp_path: Path,
) -> None:
    # Given
    store = Store(tmp_path)
    original_recipe = recipe(GenerationSettings(seed=31))
    profile = store.save_voice("Chosen reference", reference_file(tmp_path), recipe=original_recipe)
    override = GenerationSettings(seed=52, temperature=0.6)
    request = GenerateRequest(
        mode="reuse",
        voice_id=profile.id,
        settings=override,
        messages=(Message(id="done", text="작업이 끝났어요."),),
    )
    calls: list[GenerateRequest] = []
    # When
    result = Workflow(store, recipe_adapters(calls)).generate(request)
    # Then
    assert result.recipe == recipe(override)
    assert calls[0].settings == override
    assert store.voice(profile.id).recipe == original_recipe


@pytest.mark.parametrize(
    ("field", "value"),
    [("model_id", "other-model"), ("model_revision", "other-revision"), ("language", "English")],
)
def test_reuse_when_profile_recipe_is_incompatible_fails_before_inference(
    tmp_path: Path, field: str, value: str
) -> None:
    # Given
    store = Store(tmp_path)
    incompatible = recipe().model_copy(update={field: value})
    profile = store.save_voice("Old recipe", reference_file(tmp_path), recipe=incompatible)
    request = GenerateRequest(
        mode="reuse", voice_id=profile.id, messages=(Message(id="done", text="끝났어요."),)
    )
    calls: list[GenerateRequest] = []
    # When
    result = Workflow(store, recipe_adapters(calls)).generate(request)
    # Then
    assert result.status == "failed"
    assert result.errors[-1].code == "incompatible_recipe"
    assert result.messages[0].attempts == 0
    assert calls == []


def test_new_profile_when_explicit_settings_are_used_persists_recipe(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path)
    request = generation_request().model_copy(update={"profile_name": "Reusable calm voice"})
    calls: list[GenerateRequest] = []
    # When
    result = Workflow(store, recipe_adapters(calls)).generate(request)
    # Then
    assert result.recipe == recipe(request.settings)
    assert result.voice_id is not None
    assert store.voice(result.voice_id).recipe == result.recipe
    assert result.request.language == "ko"


def test_resume_when_profile_recipe_changes_uses_original_job_snapshot(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path)
    saved_recipe = recipe(GenerationSettings(seed=31))
    profile = store.save_voice("Chosen reference", reference_file(tmp_path), recipe=saved_recipe)
    calls: list[GenerateRequest] = []
    workflow = Workflow(store, recipe_adapters(calls))
    original = workflow.generate(
        GenerateRequest(
            mode="reuse", voice_id=profile.id, messages=(Message(id="done", text="끝났어요."),)
        )
    )
    changed_profile = profile.model_copy(update={"recipe": recipe(GenerationSettings(seed=99))})
    store.atomic_write(store.root / "voices" / profile.id / "profile.json", changed_profile)
    # When
    result = workflow.resume(ResumeRequest(job_id=original.job_id))
    # Then
    assert result.recipe == saved_recipe
    assert result.messages == original.messages
    assert len(calls) == 1


def test_reuse_when_saved_language_is_an_alias_records_canonical_recipe(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path)
    saved_recipe = recipe().model_copy(update={"language": "ko"})
    profile = store.save_voice("Chosen reference", reference_file(tmp_path), recipe=saved_recipe)
    calls: list[GenerateRequest] = []
    # When
    result = Workflow(store, recipe_adapters(calls)).generate(
        GenerateRequest(
            mode="reuse", voice_id=profile.id, messages=(Message(id="done", text="끝났어요."),)
        )
    )
    # Then
    assert result.recipe == recipe()
    assert calls[0].language == "Korean"


def test_resume_when_snapshot_settings_differ_from_original_request_refuses_them(
    tmp_path: Path,
) -> None:
    # Given
    store = Store(tmp_path)
    calls: list[GenerateRequest] = []
    workflow = Workflow(store, recipe_adapters(calls))
    original = workflow.generate(generation_request())
    _ = store.save(original.model_copy(update={"recipe": recipe(GenerationSettings(seed=999))}))
    # When
    result = workflow.resume(ResumeRequest(job_id=original.job_id))
    # Then
    assert result.status == "failed"
    assert result.errors[-1].code == "incompatible_recipe"
    assert len(calls) == 1


def test_transport_when_saved_voice_is_reused_restores_recipe_for_new_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    calls: list[GenerateRequest] = []
    adapters = recipe_adapters(calls)
    monkeypatch.setattr(engine, "design_reference", adapters.design)
    monkeypatch.setattr(engine, "synthesize", adapters.synthesize)
    monkeypatch.setattr(engine, "validate_output", adapters.validate)
    first = engine.run(
        generation_request()
        .model_copy(update={"profile_name": "Reusable voice"})
        .model_dump_json(),
        tmp_path,
    )
    assert isinstance(first, JobResult)
    assert first.voice_id is not None
    request = GenerateRequest(
        mode="reuse",
        voice_id=first.voice_id,
        messages=(Message(id="attention", text="확인이 필요해요."),),
    )
    # When
    result = engine.run(request.model_dump_json(), tmp_path)
    # Then
    assert isinstance(result, JobResult)
    assert result.status == "complete"
    assert result.recipe == first.recipe
    assert len(calls) == 2
    assert calls[1].settings == calls[0].settings
    assert calls[1].messages == request.messages
