import pytest

from voice_of_karina.backends.languages import asr_language, tts_language
from voice_of_karina.errors import VoiceError


@pytest.mark.parametrize(
    ("source", "tts", "asr"),
    [
        ("Korean", "Korean", "ko"),
        ("ko", "Korean", "ko"),
        ("ENGLISH", "English", "en"),
        ("en", "English", "en"),
        ("Chinese", "Chinese", "zh"),
        ("ja", "Japanese", "ja"),
        ("de", "German", "de"),
        ("fr", "French", "fr"),
        ("Russian", "Russian", "ru"),
        ("Portuguese", "Portuguese", "pt"),
        ("es", "Spanish", "es"),
        ("Italian", "Italian", "it"),
        ("auto", "Auto", None),
    ],
)
def test_languages_are_consistent_when_mapping_model_apis(
    source: str, tts: str, asr: str | None
) -> None:
    # Given / When
    actual_tts, actual_asr = tts_language(source), asr_language(source)
    # Then
    assert actual_tts == tts
    assert actual_asr == asr


def test_language_rejects_unknown_name_before_inference() -> None:
    # Given / When
    with pytest.raises(VoiceError) as failure:
        _ = asr_language("not-a-language")
    # Then
    assert failure.value.code == "unsupported_language"
