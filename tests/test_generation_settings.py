import pytest
from pydantic import ValidationError

from voice_of_karina.errors import VoiceError
from voice_of_karina.generation_settings import (
    GenerationSettings,
    SynthesisEvidence,
    VoiceRecipe,
    settings_for_attempt,
)


def test_first_attempt_preserves_selected_generation_recipe() -> None:
    # Given
    settings = GenerationSettings(seed=42, temperature=0.7, top_p=0.95, top_k=40)
    # When
    effective = settings_for_attempt(settings, "attention", 1)
    # Then
    assert effective == settings


@pytest.mark.parametrize("seed", [0, 42, 2**32 - 1])
def test_retries_have_stable_distinct_seeds_without_changing_sampling_controls(seed: int) -> None:
    # Given
    settings = GenerationSettings(seed=seed, temperature=0.6, repetition_penalty=1.8)
    # When
    attempts = tuple(settings_for_attempt(settings, "attention", index) for index in (1, 2, 3))
    # Then
    assert len({item.seed for item in attempts}) == 3
    assert all(0 <= item.seed < 2**32 for item in attempts)
    assert all(item.model_copy(update={"seed": seed}) == settings for item in attempts)
    assert settings_for_attempt(settings, "attention", 3) == attempts[2]


def test_retry_seed_is_specific_to_message_without_requiring_batch_order() -> None:
    # Given
    settings = GenerationSettings()
    # When
    attention = settings_for_attempt(settings, "attention", 2)
    done = settings_for_attempt(settings, "done", 2)
    # Then
    assert attention.seed != done.seed


@pytest.mark.parametrize("attempt", [0, -1, 4])
def test_attempt_numbers_outside_generation_budget_are_refused(attempt: int) -> None:
    # Given
    settings = GenerationSettings()
    # When / Then
    with pytest.raises(VoiceError, match="attempt"):
        _ = settings_for_attempt(settings, "attention", attempt)


@pytest.mark.parametrize(
    "raw",
    [
        '{"seed": -1}',
        '{"seed": 4294967296}',
        '{"seed": true}',
        '{"temperature": 0}',
        '{"temperature": 2.1}',
        '{"temperature": NaN}',
        '{"top_p": 0}',
        '{"top_p": 1.1}',
        '{"top_k": 0}',
        '{"top_k": 101}',
        '{"repetition_penalty": 1.05}',
        '{"repetition_penalty": 2.6}',
        '{"speed": 0.9}',
    ],
)
def test_settings_reject_unsupported_or_ineffective_values(raw: str) -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        _ = GenerationSettings.model_validate_json(raw)


def test_recipe_and_synthesis_evidence_round_trip_without_losing_applied_settings() -> None:
    # Given
    settings = GenerationSettings(seed=100)
    recipe = VoiceRecipe(settings=settings, model_id="example/voice-model", model_revision="abc123")
    evidence = SynthesisEvidence(
        settings=settings,
        text="확인해 주세요.",
        audio_sha256="a" * 64,
        runtime="mlx-audio==0.5.4; mlx==0.30.0; Darwin arm64",
    )
    # When
    restored_recipe = VoiceRecipe.model_validate_json(recipe.model_dump_json())
    restored_evidence = SynthesisEvidence.model_validate_json(evidence.model_dump_json())
    # Then
    assert restored_recipe == recipe
    assert restored_evidence == evidence
    assert restored_recipe.language == "Korean"


@pytest.mark.parametrize("runtime", ["", " "])
def test_evidence_rejects_unknown_runtime(runtime: str) -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        _ = SynthesisEvidence(
            settings=GenerationSettings(), text="안녕", audio_sha256="a" * 64, runtime=runtime
        )
