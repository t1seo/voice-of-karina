"""One language vocabulary shared by synthesis and transcription adapters."""

from dataclasses import dataclass
from typing import Final

from voice_of_karina.errors import VoiceError


@dataclass(frozen=True, slots=True)
class _Language:
    name: str
    code: str | None


_LANGUAGES: Final = (
    _Language("Korean", "ko"),
    _Language("English", "en"),
    _Language("Chinese", "zh"),
    _Language("Japanese", "ja"),
    _Language("German", "de"),
    _Language("French", "fr"),
    _Language("Russian", "ru"),
    _Language("Portuguese", "pt"),
    _Language("Spanish", "es"),
    _Language("Italian", "it"),
    _Language("Auto", None),
)
_INDEX: Final = {
    alias: language
    for language in _LANGUAGES
    for alias in (language.name.lower(), language.code)
    if alias is not None
}


def _lookup(language: str) -> _Language:
    try:
        return _INDEX[language.strip().lower()]
    except KeyError as error:
        raise VoiceError(
            "unsupported_language", f"Unsupported voice language: {language}"
        ) from error


def asr_language(language: str) -> str | None:
    """Map supported names or ISO codes to Whisper; auto enables detection."""
    return _lookup(language).code


def tts_language(language: str) -> str:
    """Map supported names or ISO codes to Qwen's canonical language names."""
    return _lookup(language).name
