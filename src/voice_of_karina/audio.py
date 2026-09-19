"""Conversational source analysis and minimally processed reference selection."""

from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile

from voice_of_karina.audio_candidates import MAX_CANDIDATES, source_candidates
from voice_of_karina.audio_io import decode_audio
from voice_of_karina.audio_metrics import (
    MAX_REFERENCE_CLIPPING,
    MIN_REFERENCE_RMS_DBFS,
    reference_score,
    signal_metrics,
)
from voice_of_karina.audio_transcription import transcribe_candidates
from voice_of_karina.contracts import (
    AnalysisResult,
    AnalyzeRequest,
    Candidate,
    GenerateRequest,
    Provenance,
    Reference,
    Source,
)
from voice_of_karina.errors import VoiceError
from voice_of_karina.sources import SCAN_SECONDS, acquire_source, file_sha256


def analyze_sources(request: AnalyzeRequest | GenerateRequest, job_dir: Path) -> AnalysisResult:
    """Return playable references and explicit uncertainty instead of guessing the speaker."""
    selection = request.source_time
    if selection is not None and selection.source_index >= len(request.sources):
        msg = "invalid_source_index"
        raise VoiceError(msg, "The selected source index does not exist.")
    job_dir.mkdir(parents=True, exist_ok=True)
    sources: list[Source] = []
    candidates: list[Candidate] = []
    warnings: list[str] = []
    for index, value in enumerate(request.sources):
        if selection is not None and index != selection.source_index:
            continue
        start = selection.start_seconds if selection is not None else 0
        duration = selection.end_seconds - start if selection is not None else SCAN_SECONDS
        try:
            source = acquire_source(
                value,
                job_dir.parent / "source_cache",
                start=start,
                duration=duration,
                strict_interval=selection is not None,
            )
            sources.append(source)
            candidates.extend(source_candidates(source, job_dir, selected=selection is not None))
        except VoiceError as error:
            warnings.append(f"Source {index + 1}: {error.message}")
    if selection is None:
        warnings.append(
            "Scan limit: first 180s per recording. Use timestamps to inspect a later interval."
        )
    ranked = tuple(
        sorted(candidates, key=lambda item: reference_score(item.metrics), reverse=True)[
            :MAX_CANDIDATES
        ]
    )
    if not ranked:
        return AnalysisResult(
            sources=tuple(sources),
            warnings=tuple(warnings),
            input_request=(
                "No usable speech reference was found. Provide 3 to 15 seconds of "
                "clear solo speech, or another link/timestamp."
            ),
        )
    transcribed, transcription_warning = transcribe_candidates(
        ranked, job_dir, language=request.language
    )
    if transcription_warning is not None:
        warnings.append(transcription_warning)
    usable = tuple(candidate for candidate in transcribed if candidate.transcript)
    needs_input = transcription_warning
    if usable and any(candidate.needs_confirmation for candidate in usable):
        needs_input = (
            "Listen to the candidate clips and select the intended speaker, "
            "or provide precise source timestamps."
        )
    if not usable and needs_input is None:
        needs_input = (
            "Speech could not be transcribed reliably. Please provide a clearer solo utterance."
        )
    return AnalysisResult(
        sources=tuple(sources),
        candidates=transcribed,
        recommended_candidate_id=usable[0].id if usable else None,
        warnings=tuple(warnings),
        input_request=needs_input,
    )


def prepare_reference(candidate: Candidate, job_dir: Path) -> Reference:
    """Persist the selected raw crop and its matching transcript without denoising."""
    if not candidate.transcript:
        msg = "unverified_reference"
        raise VoiceError(
            msg, "A reliable reference transcript is required; choose a clearer utterance."
        )
    source = Path(candidate.audio_path)
    metrics = signal_metrics(decode_audio(source))
    if (
        metrics.rms_dbfs is None
        or metrics.rms_dbfs < MIN_REFERENCE_RMS_DBFS
        or (metrics.clipping_ratio or 0) > MAX_REFERENCE_CLIPPING
    ):
        msg = "invalid_reference"
        raise VoiceError(msg, "The selected clip is silent or clipped; select another candidate.")
    digest = file_sha256(source)
    destination = job_dir / "references" / f"{digest}.wav"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=destination.parent, suffix=".wav", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        _ = shutil.copyfile(source, temporary_path)
        _ = temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return Reference(
        audio_path=str(destination.resolve()),
        sha256=digest,
        transcript=candidate.transcript,
        provenance=Provenance(
            kind="mimic",
            source=candidate.source,
            source_id=candidate.source_id,
            start_seconds=candidate.start_seconds,
            end_seconds=candidate.end_seconds,
        ),
        preparation=("original", "mono", "native_sample_rate"),
    )
