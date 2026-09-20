"""Load contained, versioned reference assets packaged with the engine."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from pydantic import ValidationError

from voice_of_karina.contracts import FrozenModel, Identifier, Reference, Text
from voice_of_karina.curation_contracts import RegisterRequest
from voice_of_karina.errors import VoiceError
from voice_of_karina.generation_settings import VoiceRecipe
from voice_of_karina.reference_registration import register_voice
from voice_of_karina.storage import safe_id

if TYPE_CHECKING:
    from voice_of_karina.curation_contracts import PresetRequest, ProfileResult
    from voice_of_karina.storage import Store

PRESET_ROOT: Final = Path(__file__).resolve().parent / "presets"


class PresetManifest(FrozenModel):
    """A portable reference uses a package-relative audio path and an explicit recipe."""

    version: Literal[1] = 1
    id: Identifier
    name: Text
    reference: Reference
    recipe: VoiceRecipe


def register_preset(request: PresetRequest, store: Store) -> ProfileResult:
    """Resolve a packaged reference and apply the same registration checks as local files."""
    directory = PRESET_ROOT / safe_id(request.preset_id)
    manifest_path = directory / "manifest.json"
    if not manifest_path.resolve().is_relative_to(PRESET_ROOT.resolve()):
        raise VoiceError("invalid_preset", "The preset manifest escapes the installed catalog.")
    try:
        preset = PresetManifest.model_validate_json(manifest_path.read_bytes())
    except FileNotFoundError as error:
        raise VoiceError(
            "unknown_preset", "This preset is not included with the installed engine."
        ) from error
    except ValidationError as error:
        raise VoiceError("invalid_preset", "The packaged reference manifest is damaged.") from error
    relative = Path(preset.reference.audio_path)
    audio = (directory / relative).resolve()
    if (
        preset.id != request.preset_id
        or relative.is_absolute()
        or not audio.is_relative_to(directory.resolve())
    ):
        raise VoiceError("invalid_preset", "The packaged reference must stay inside its preset.")
    return register_voice(
        RegisterRequest(
            name=request.profile_name or preset.name,
            reference=preset.reference.model_copy(update={"audio_path": str(audio)}),
            recipe=preset.recipe,
        ),
        store,
    )
