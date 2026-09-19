"""Generate exact requested text and atomically write standard PCM WAVs."""

import math
import sys
import wave
from array import array
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from voice_of_karina.contracts import GeneratedAudio
from voice_of_karina.errors import VoiceError

from .interfaces import CloneModel, DesignModel, GenerationChunk
from .models import CLONE_MODEL, DESIGN_MODEL, token_limit
from .protocol import AudioResponse, CloneTask, DesignTask


@dataclass(frozen=True, slots=True)
class AudioStats:
    """Measurements derived from samples actually written to disk."""

    sample_rate: int
    duration_seconds: float


def write_audio(chunks: Iterable[GenerationChunk], destination: Path) -> AudioStats:
    """Reject empty/nonfinite output before publishing a playable WAV."""
    samples: list[float] = []
    sample_rate: int | None = None
    for chunk in chunks:
        if chunk.sample_rate <= 0 or (sample_rate is not None and sample_rate != chunk.sample_rate):
            raise VoiceError("invalid_audio", "The model returned inconsistent sample rates.")
        sample_rate = chunk.sample_rate
        samples.extend(chunk.audio.reshape(-1).tolist())
    if sample_rate is None or not samples or not all(math.isfinite(value) for value in samples):
        raise VoiceError(
            "invalid_audio", "invalid_audio: The model returned empty or nonfinite audio."
        )
    pcm = array("h", (round(max(-1.0, min(1.0, sample)) * 32767) for sample in samples))
    if sys.byteorder != "little":
        pcm.byteswap()
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_suffix(".wav.pending")
    with wave.open(str(pending), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())
    _ = pending.replace(destination)
    return AudioStats(sample_rate=sample_rate, duration_seconds=len(samples) / sample_rate)


def clone_batch(task: CloneTask, model: CloneModel) -> AudioResponse:
    """Reuse one loaded Base model and the same reference for the entire batch."""
    artifacts: list[GeneratedAudio] = []
    for message in task.messages:
        started = perf_counter()
        path = task.output_dir / f"{message.id}.wav"
        chunks = model.generate(
            text=message.text,
            lang_code=task.language,
            ref_audio=task.reference.audio_path,
            ref_text=task.reference.transcript,
            stream=False,
            max_tokens=token_limit(message.text),
        )
        stats = write_audio(chunks, path)
        artifact = GeneratedAudio(
            message_id=message.id,
            path=str(path),
            sample_rate=stats.sample_rate,
            duration_seconds=stats.duration_seconds,
            model_id=CLONE_MODEL.repository,
            model_revision=CLONE_MODEL.revision,
            elapsed_seconds=perf_counter() - started,
            language=task.language,
            reference=task.reference,
        )
        manifest = path.with_suffix(".audio.json")
        pending = manifest.with_suffix(".json.pending")
        _ = pending.write_text(artifact.model_dump_json(), encoding="utf-8")
        _ = pending.replace(manifest)
        artifacts.append(artifact)
    return AudioResponse(audio=tuple(artifacts))


def design_audio(task: DesignTask, model: DesignModel) -> AudioResponse:
    """Use VoiceDesign's language parameter without unsupported speed controls."""
    started = perf_counter()
    chunks = model.generate_voice_design(
        text=task.text,
        language=task.language,
        instruct=task.description,
        stream=False,
        max_tokens=token_limit(task.text),
    )
    stats = write_audio(chunks, task.output_path)
    return AudioResponse(
        audio=(
            GeneratedAudio(
                message_id="reference",
                path=str(task.output_path),
                sample_rate=stats.sample_rate,
                duration_seconds=stats.duration_seconds,
                model_id=DESIGN_MODEL.repository,
                model_revision=DESIGN_MODEL.revision,
                elapsed_seconds=perf_counter() - started,
                language=task.language,
            ),
        )
    )
