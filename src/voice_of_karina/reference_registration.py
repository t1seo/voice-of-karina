"""Register exact reference bytes only after independent signal and text verification."""

from __future__ import annotations

import hashlib
import shutil
import wave
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Final

from voice_of_karina.audio_io import decode_audio
from voice_of_karina.audio_metrics import (
    MAX_REFERENCE_CLIPPING,
    MAX_REFERENCE_SECONDS,
    MIN_REFERENCE_RMS_DBFS,
    MIN_REFERENCE_SECONDS,
    signal_metrics,
)
from voice_of_karina.audio_transcription import uncertain_transcript
from voice_of_karina.backends.languages import asr_language
from voice_of_karina.backends.models import CLONE_MODEL
from voice_of_karina.backends.stt import transcribe_audio
from voice_of_karina.contracts import Provenance, QualityOptions, Reference
from voice_of_karina.curation_contracts import ProfileResult, RegisterRequest
from voice_of_karina.errors import VoiceError
from voice_of_karina.generation_settings import GenerationSettings, VoiceRecipe
from voice_of_karina.quality import normalize_text, text_error_rate
from voice_of_karina.storage import file_digest

if TYPE_CHECKING:
    from voice_of_karina.curation_contracts import RegisterCandidateRequest
    from voice_of_karina.storage import Store

MIN_SAMPLE_RATE: Final = 8000
MAX_SAMPLE_RATE: Final = 192000
MAX_REFERENCE_BYTES: Final = 16 * 1024 * 1024


def _validate_wav(path: Path) -> None:
    try:
        with wave.open(str(path), "rb") as source:
            rate = source.getframerate()
            frames = source.getnframes()
            if (
                source.getnchannels() != 1
                or not MIN_SAMPLE_RATE <= rate <= MAX_SAMPLE_RATE
                or not MIN_REFERENCE_SECONDS <= frames / rate <= MAX_REFERENCE_SECONDS
                or source.getsampwidth() not in (2, 3, 4)
            ):
                raise VoiceError(
                    "invalid_reference",
                    "Use a mono PCM WAV lasting 3 to 15 seconds at its native sample rate.",
                )
            if len(source.readframes(frames)) != frames * source.getsampwidth():
                raise VoiceError("invalid_reference", "The supplied WAV is truncated.")
    except (wave.Error, EOFError, OSError) as error:
        raise VoiceError(
            "invalid_reference", "The supplied reference is not a readable PCM WAV."
        ) from error
    metrics = signal_metrics(decode_audio(path, max_seconds=MAX_REFERENCE_SECONDS + 1))
    if (
        metrics.rms_dbfs is None
        or metrics.rms_dbfs < MIN_REFERENCE_RMS_DBFS
        or (metrics.clipping_ratio or 0) > MAX_REFERENCE_CLIPPING
    ):
        raise VoiceError("invalid_reference", "The supplied reference is silent or clipped.")


def register_voice(request: RegisterRequest, store: Store) -> ProfileResult:
    """Freeze, independently verify, and preserve the supplied reference without recropping."""
    reference = request.reference
    identity = "register-" + hashlib.sha256(reference.sha256.encode()).hexdigest()[:24]
    with store.lock(identity):
        source = Path(reference.audio_path).expanduser().resolve()
        if (
            not source.is_file()
            or source.stat().st_size > MAX_REFERENCE_BYTES
            or file_digest(source) != reference.sha256
        ):
            raise VoiceError("invalid_reference", "The supplied reference is missing or changed.")
        frozen = store.job_dir(identity) / "reference.wav"
        if not frozen.resolve().is_relative_to(store.root):
            raise VoiceError("invalid_path", "The registration reference escapes the voice store.")
        with NamedTemporaryFile(dir=frozen.parent, suffix=".wav", delete=False) as temporary:
            temporary_path = Path(temporary.name)
        try:
            _ = shutil.copyfile(source, temporary_path)
            if file_digest(temporary_path) != reference.sha256:
                raise VoiceError(
                    "changed_reference", "The supplied reference changed while copying."
                )
            _ = temporary_path.replace(frozen)
        finally:
            temporary_path.unlink(missing_ok=True)
        _validate_wav(frozen)
        language = request.recipe.language if request.recipe is not None else "Korean"
        recognition = transcribe_audio(frozen, language=asr_language(language))
        expected = normalize_text(reference.transcript)
        observed = normalize_text(recognition.text)
        if (
            not expected
            or uncertain_transcript(recognition)
            or not observed.endswith(expected[-4:])
            or text_error_rate(reference.transcript, recognition.text)
            > QualityOptions().max_text_error_rate
        ):
            raise VoiceError(
                "unverified_reference",
                "Independent transcription does not reliably match the supplied reference text.",
            )
        if file_digest(frozen) != reference.sha256:
            raise VoiceError(
                "changed_reference", "The frozen reference changed during verification."
            )
        profile = store.save_voice(
            request.name,
            reference.model_copy(update={"audio_path": str(frozen)}),
            recipe=request.recipe,
        )
        return ProfileResult(voice=profile)


def register_candidate(request: RegisterCandidateRequest, store: Store) -> ProfileResult:
    """Register a selected analysis candidate using its saved transcript and PCM identity."""
    previous = store.load(request.analysis_job_id)
    if previous.analysis is None:
        raise VoiceError("missing_analysis", "That job has no source analysis to register.")
    candidate = next(
        (item for item in previous.analysis.candidates if item.id == request.candidate_id), None
    )
    if candidate is None:
        raise VoiceError("unknown_candidate", "Choose a candidate returned by this analysis.")
    if not candidate.sha256 or not candidate.transcript:
        raise VoiceError(
            "unverified_candidate",
            "Rerun source analysis to bind this candidate's transcript to its exact audio.",
        )
    reference = Reference(
        audio_path=candidate.audio_path,
        sha256=candidate.sha256,
        transcript=candidate.transcript,
        provenance=Provenance(
            kind="mimic",
            source=candidate.source,
            source_id=candidate.source_id,
            start_seconds=candidate.start_seconds,
            end_seconds=candidate.end_seconds,
        ),
        preparation=("original", "mono", "native_sample_rate")
        + (("pause_bounded",) if candidate.pause_bounded else ()),
    )
    recipe = request.recipe or VoiceRecipe(
        language=previous.request.language,
        settings=GenerationSettings(),
        model_id=CLONE_MODEL.repository,
        model_revision=CLONE_MODEL.revision,
    )
    return register_voice(
        RegisterRequest(name=request.name, reference=reference, recipe=recipe), store
    )
