"""Snapshot compatible saved recipes without changing the original user request."""

from voice_of_karina.backends.languages import tts_language
from voice_of_karina.backends.models import CLONE_MODEL
from voice_of_karina.contracts import GenerateRequest
from voice_of_karina.errors import VoiceError
from voice_of_karina.generation_settings import VoiceRecipe


def compatible_recipe(recipe: VoiceRecipe, language: str) -> None:
    """Refuse silent changes to the model, checkpoint, or selected output language."""
    if (
        recipe.model_id != CLONE_MODEL.repository
        or recipe.model_revision != CLONE_MODEL.revision
        or tts_language(recipe.language) != tts_language(language)
    ):
        raise VoiceError(
            "incompatible_recipe",
            "This saved recipe requires a different model, checkpoint, or language.",
        )


def resolve_recipe(
    request: GenerateRequest, saved: VoiceRecipe | None = None
) -> VoiceRecipe | None:
    """Explicit settings override sampling while preserving compatibility checks."""
    if saved is not None:
        compatible_recipe(saved, request.language)
    if request.settings is None:
        return (
            saved.model_copy(update={"language": tts_language(saved.language)})
            if saved is not None
            else None
        )
    return VoiceRecipe(
        settings=request.settings,
        language=tts_language(request.language),
        model_id=CLONE_MODEL.repository,
        model_revision=CLONE_MODEL.revision,
    )
