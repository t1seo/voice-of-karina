"""Build traceable candidate clips from measured utterance windows."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

from voice_of_karina.audio_io import crop_audio, decode_audio
from voice_of_karina.audio_metrics import (
    ANALYSIS_RATE,
    MAX_REFERENCE_CLIPPING,
    MAX_REFERENCE_SECONDS,
    MIN_REFERENCE_RMS_DBFS,
    MIN_REFERENCE_SECONDS,
    MIN_SPEECH_RATIO,
    NOISY_FLOOR_DBFS,
    reference_metrics,
    reference_score,
    speech_frames,
    utterance_windows,
)
from voice_of_karina.contracts import Candidate, Source
from voice_of_karina.errors import VoiceError

MAX_CANDIDATES: Final = 4


def source_candidates(source: Source, job_dir: Path, *, selected: bool) -> tuple[Candidate, ...]:
    """Keep native reference samples separate from the 16kHz analysis representation."""
    path = Path(source.audio_path)
    analysis = decode_audio(path, sample_rate=ANALYSIS_RATE)
    if selected:
        if not MIN_REFERENCE_SECONDS <= source.duration_seconds <= MAX_REFERENCE_SECONDS:
            msg = "short_reference"
            raise VoiceError(msg, "Choose one continuous utterance lasting 3 to 15 seconds.")
        windows = ((0.0, source.duration_seconds),)
    else:
        windows = utterance_windows(speech_frames(analysis))
    ranked: list[Candidate] = []
    for start, end in windows:
        segment = analysis.samples[int(start * ANALYSIS_RATE) : int(end * ANALYSIS_RATE)]
        metrics = reference_metrics(type(analysis)(segment, ANALYSIS_RATE))
        if (
            (metrics.speech_ratio or 0) < MIN_SPEECH_RATIO
            or metrics.rms_dbfs is None
            or metrics.rms_dbfs < MIN_REFERENCE_RMS_DBFS
        ):
            continue
        if (metrics.clipping_ratio or 0) > MAX_REFERENCE_CLIPPING:
            continue
        identifier = hashlib.sha256(f"{source.sha256}:{start:.6f}:{end:.6f}".encode()).hexdigest()[
            :20
        ]
        clip = job_dir / "candidates" / f"candidate-{identifier}.wav"
        warnings = [
            (
                "Speaker identity and overlapping voices require listening; "
                "VAD does not identify people."
            )
        ]
        if metrics.noise_floor_dbfs is None:
            warnings.append(
                "Background noise estimate is unknown: no measurable non-speech baseline."
            )
        elif metrics.noise_floor_dbfs > NOISY_FLOOR_DBFS:
            warnings.append(
                "Elevated non-speech background level; a cleaner recording may sound more natural."
            )
        ranked.append(
            Candidate(
                id=f"candidate-{identifier}",
                source_id=source.id,
                source=source.original,
                audio_path=str(clip.resolve()),
                start_seconds=start + source.offset_seconds,
                end_seconds=end + source.offset_seconds,
                metrics=metrics,
                warnings=tuple(warnings),
                needs_confirmation=not selected,
            )
        )
    selected_candidates = tuple(
        sorted(ranked, key=lambda candidate: reference_score(candidate.metrics), reverse=True)[
            :MAX_CANDIDATES
        ]
    )
    for candidate in selected_candidates:
        _ = crop_audio(
            path,
            Path(candidate.audio_path),
            candidate.start_seconds - source.offset_seconds,
            candidate.end_seconds - source.offset_seconds,
        )
    return selected_candidates
