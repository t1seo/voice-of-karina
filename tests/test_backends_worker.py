import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from voice_of_karina.backends.interfaces import SpeechSegment
from voice_of_karina.backends.native import transcribe_batch
from voice_of_karina.backends.protocol import FailureResponse, TranscribeTask


@dataclass(frozen=True, slots=True)
class Speech:
    text: str
    segments: list[SpeechSegment]
    language: str = "ko"


@dataclass(frozen=True, slots=True)
class SpeechCall:
    path: str
    language: str | None
    temperature: float
    condition_on_previous_text: bool
    word_timestamps: bool
    verbose: bool | None


@dataclass(frozen=True, slots=True)
class SpeechModel:
    calls: list[SpeechCall] = field(default_factory=list)

    def generate(
        self,
        audio: str,
        *,
        language: str | None,
        temperature: float,
        condition_on_previous_text: bool,
        word_timestamps: bool,
        verbose: bool | None,
    ) -> Speech:
        self.calls.append(
            SpeechCall(
                audio, language, temperature, condition_on_previous_text, word_timestamps, verbose
            )
        )
        return Speech(
            text="작업이 끝났어요.",
            language=language or "ko",
            segments=[
                {
                    "start": 0.0,
                    "end": 1.25,
                    "text": "작업이 끝났어요.",
                    "avg_logprob": -0.2,
                    "no_speech_prob": 0.03,
                }
            ],
        )


@pytest.mark.parametrize("language", ["ko", "en", None])
def test_transcription_preserves_confidence_when_reusing_loaded_model(
    tmp_path: Path, language: str | None
) -> None:
    # Given
    task = TranscribeTask(
        paths=(tmp_path / "first.wav", tmp_path / "second.wav"), language=language
    )
    model = SpeechModel()
    # When
    response = transcribe_batch(task, model)
    # Then
    assert len(response.transcripts) == 2
    assert [call.path for call in model.calls] == [str(path) for path in task.paths]
    assert all(
        call.temperature == 0 and not call.condition_on_previous_text for call in model.calls
    )
    assert all(call.language == language and not call.word_timestamps for call in model.calls)
    assert response.transcripts[0].segments[0].no_speech_prob == 0.03
    assert response.transcripts[0].segments[0].avg_logprob == -0.2


def test_worker_reports_invalid_request_without_loading_model_when_json_is_invalid() -> None:
    # Given
    raw_request = "{}"
    # When
    result = subprocess.run(
        [sys.executable, "-m", "voice_of_karina.backends.worker"],
        input=raw_request,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    # Then
    response = FailureResponse.model_validate_json(result.stdout)
    assert result.returncode == 1
    assert response.code == "invalid_worker_request"
    assert "Traceback" not in result.stdout
