from __future__ import annotations

import math
import wave
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from voice_of_karina.backends.generation import clone_batch, design_audio, write_audio
from voice_of_karina.backends.protocol import CloneTask, DesignTask
from voice_of_karina.contracts import GeneratedAudio, Message, Provenance, Reference
from voice_of_karina.errors import VoiceError


@dataclass(frozen=True, slots=True)
class Samples:
    values: tuple[float, ...]

    def reshape(self, _size: int) -> Samples:
        return self

    def tolist(self) -> list[float]:
        return list(self.values)


@dataclass(frozen=True, slots=True)
class Chunk:
    audio: Samples
    sample_rate: int = 24_000


@dataclass(frozen=True, slots=True)
class CloneCall:
    text: str
    lang_code: str
    ref_audio: str
    ref_text: str
    stream: bool
    max_tokens: int


@dataclass(frozen=True, slots=True)
class CloneModel:
    calls: list[CloneCall] = field(default_factory=list)

    def generate(
        self,
        *,
        text: str,
        lang_code: str,
        ref_audio: str,
        ref_text: str,
        stream: bool,
        max_tokens: int,
    ) -> tuple[Chunk, ...]:
        self.calls.append(CloneCall(text, lang_code, ref_audio, ref_text, stream, max_tokens))
        return (Chunk(Samples((0.0, 0.3, -0.3) * 100)),)


@dataclass(frozen=True, slots=True)
class DesignCall:
    text: str
    language: str
    instruct: str
    stream: bool
    max_tokens: int


@dataclass(frozen=True, slots=True)
class DesignModel:
    calls: list[DesignCall] = field(default_factory=list)

    def generate_voice_design(
        self, *, text: str, language: str, instruct: str, stream: bool, max_tokens: int
    ) -> tuple[Chunk, ...]:
        self.calls.append(DesignCall(text, language, instruct, stream, max_tokens))
        return (Chunk(Samples((0.0, 0.2, -0.2) * 100)),)


@pytest.mark.parametrize("language", ["Korean", "English"])
def test_clone_preserves_every_message_and_reference_when_generating_batch(
    tmp_path: Path, language: str
) -> None:
    # Given
    reference = Reference(
        audio_path=str(tmp_path / "reference.wav"),
        sha256="a" * 64,
        transcript="참조하는 원래 목소리예요.",
        provenance=Provenance(kind="mimic", source="sample.wav"),
    )
    messages = (
        Message(id="first", text="작업 끝났어요."),
        Message(id="second", text="확인해 주세요!"),
    )
    task = CloneTask(messages=messages, reference=reference, output_dir=tmp_path, language=language)
    model = CloneModel()
    # When
    result = clone_batch(task, model)
    # Then
    assert [call.text for call in model.calls] == [message.text for message in messages]
    assert all(call.lang_code == language and not call.stream for call in model.calls)
    assert all(call.ref_audio == reference.audio_path for call in model.calls)
    assert all(call.ref_text == reference.transcript for call in model.calls)
    assert all(0 < call.max_tokens <= 512 for call in model.calls)
    assert [item.message_id for item in result.audio] == ["first", "second"]
    for item in result.audio:
        assert item.language == language
        manifest = tmp_path / f"{item.message_id}.audio.json"
        assert GeneratedAudio.model_validate_json(manifest.read_text()) == item
        with wave.open(item.path, "rb") as audio:
            assert audio.getframerate() == 24_000
            assert audio.getnframes() == 300


@pytest.mark.parametrize("language", ["Korean", "English"])
def test_design_uses_description_and_language_when_creating_reference(
    tmp_path: Path, language: str
) -> None:
    # Given
    task = DesignTask(
        text="안녕하세요. 편안하게 말씀드릴게요.",
        description="차분한 한국어 여성 목소리",
        language=language,
        output_path=tmp_path / "reference.wav",
    )
    model = DesignModel()
    # When
    result = design_audio(task, model)
    # Then
    assert model.calls == [
        DesignCall(task.text, language, task.description, stream=False, max_tokens=256)
    ]
    assert Path(result.audio[0].path).is_file()
    assert result.audio[0].duration_seconds == 300 / 24_000
    assert result.audio[0].language == language


@pytest.mark.parametrize("values", [(), (math.nan,), (math.inf,)])
def test_output_rejects_invalid_model_samples_when_writing(
    tmp_path: Path, values: tuple[float, ...]
) -> None:
    # Given
    chunks = (Chunk(Samples(values)),)
    # When / Then
    with pytest.raises(VoiceError, match="invalid_audio"):
        _ = write_audio(chunks, tmp_path / "bad.wav")
    assert not (tmp_path / "bad.wav").exists()


def test_output_rejects_mixed_sample_rates_when_concatenating(tmp_path: Path) -> None:
    # Given
    chunks = (Chunk(Samples((0.2,)), 24_000), Chunk(Samples((0.3,)), 16_000))
    # When / Then
    with pytest.raises(VoiceError, match="sample rate"):
        _ = write_audio(chunks, tmp_path / "bad.wav")
