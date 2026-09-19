"""Real model loads and dispatch, confined to a short-lived child process."""

import sys
from pathlib import Path
from time import perf_counter
from typing import assert_never

from .generation import clone_batch, design_audio
from .interfaces import SpeechModel
from .models import CLONE_MODEL, DESIGN_MODEL, STT_MODEL, ModelSpec
from .protocol import (
    AudioResponse,
    CloneTask,
    DesignTask,
    TranscribeTask,
    Transcript,
    TranscriptResponse,
    TranscriptSegment,
    WorkerTask,
)


def transcribe_batch(task: TranscribeTask, model: SpeechModel) -> TranscriptResponse:
    """Retain source order and ASR confidence while avoiding context carryover."""
    transcripts: list[Transcript] = []
    for path in task.paths:
        result = model.generate(
            str(path),
            language=task.language,
            temperature=0.0,
            condition_on_previous_text=False,
            word_timestamps=False,
            verbose=None,
        )
        transcripts.append(
            Transcript(
                text=result.text,
                language=result.language or "",
                segments=tuple(
                    TranscriptSegment.model_validate(segment) for segment in result.segments or []
                ),
            )
        )
    return TranscriptResponse(transcripts=tuple(transcripts))


def resolve_model(spec: ModelSpec) -> Path:
    """Resolve an immutable Hugging Face revision before loading any weights."""
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(repo_id=spec.repository, revision=spec.revision))


def execute(task: WorkerTask) -> AudioResponse | TranscriptResponse:
    """Load exactly one model for this child process and requested batch."""
    match task:
        case CloneTask():
            from mlx_audio.tts.utils import load_model

            model_path = resolve_model(CLONE_MODEL)
            started = perf_counter()
            model = load_model(model_path)
            _ = sys.stderr.write(f"Base model loaded in {perf_counter() - started:.2f}s\n")
            return clone_batch(task, model)
        case DesignTask():
            from mlx_audio.tts.utils import load_model

            model_path = resolve_model(DESIGN_MODEL)
            started = perf_counter()
            model = load_model(model_path)
            _ = sys.stderr.write(f"VoiceDesign model loaded in {perf_counter() - started:.2f}s\n")
            return design_audio(task, model)
        case TranscribeTask():
            from mlx_audio.stt import load

            model_path = resolve_model(STT_MODEL)
            started = perf_counter()
            speech_model = load(model_path)
            _ = sys.stderr.write(f"Whisper model loaded in {perf_counter() - started:.2f}s\n")
            return transcribe_batch(task, speech_model)
        case _:
            assert_never(task)
