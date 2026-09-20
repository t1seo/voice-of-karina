"""Atomic audition ledgers and byte-preserving, contained reference snapshots."""

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from voice_of_karina.audition_models import AuditionResult, trial_matrix
from voice_of_karina.backends.languages import tts_language
from voice_of_karina.curation_contracts import AuditionRequest
from voice_of_karina.errors import VoiceError
from voice_of_karina.storage import Store, file_digest
from voice_of_karina.workflow_models import VoiceProfile


def canonical_request(request: AuditionRequest) -> AuditionRequest:
    """Input ordering and an unused base seed cannot create a fresh attempt budget."""
    seeds = tuple(sorted(request.seeds))
    return request.model_copy(
        update={
            "voice_ids": tuple(sorted(request.voice_ids)),
            "messages": tuple(sorted(request.messages, key=lambda message: message.id)),
            "seeds": seeds,
            "settings": request.settings.model_copy(update={"seed": seeds[0]}),
            "language": tts_language(request.language),
        }
    )


@dataclass(frozen=True, slots=True)
class AuditionStore:
    """Use the shared store lock while keeping comparison records distinct from jobs."""

    store: Store

    def identity(self, request: AuditionRequest) -> str:
        """Bind the finite matrix to the storage namespace and operation version."""
        canonical = canonical_request(request)
        content = f"{self.store.cache_key}\naudition-v1\n{canonical.model_dump_json()}"
        return "audition-" + hashlib.sha256(content.encode()).hexdigest()[:24]

    def path(self, job_id: str, *parts: str) -> Path:
        """Reject symlinks and paths that would alias another study's files."""
        directory = self.store.job_dir(job_id)
        path = directory.joinpath(*parts)
        if (
            directory.resolve() != directory
            or path.resolve() != path
            or not path.is_relative_to(directory)
        ):
            raise VoiceError("invalid_path", "The audition path escapes or aliases its directory.")
        return path

    def exists(self, job_id: str) -> bool:
        """A lock-only directory is not a persisted comparison."""
        return self.path(job_id, "audition.json").is_file()

    def load(self, job_id: str) -> AuditionResult:
        """Read a complete validated ledger without creating a directory."""
        try:
            state = AuditionResult.model_validate_json(
                self.path(job_id, "audition.json").read_bytes()
            )
        except FileNotFoundError as error:
            raise VoiceError(
                "unknown_audition", "No saved audition has this identifier."
            ) from error
        except ValidationError as error:
            raise VoiceError("invalid_audition", "The saved audition ledger is damaged.") from error
        if state.job_id != job_id or self.identity(state.request) != job_id:
            raise VoiceError(
                "invalid_audition", "Saved audition identity differs from its request."
            )
        if canonical_request(state.request) != state.request:
            raise VoiceError("invalid_audition", "The saved audition request is not canonical.")
        return state

    def save(self, state: AuditionResult) -> AuditionResult:
        """Flush each trial transition before the next external model call."""
        self.store.atomic_write(self.path(state.job_id, "audition.json"), state)
        return state

    def initialize(self, request: AuditionRequest) -> AuditionResult:
        """Freeze all source profiles before allocating the first synthesis attempt."""
        canonical = canonical_request(request)
        job_id = self.identity(canonical)
        profiles = tuple(
            self._freeze(job_id, self.store.voice(voice_id)) for voice_id in canonical.voice_ids
        )
        return self.save(
            AuditionResult(
                job_id=job_id,
                request=canonical,
                profiles=profiles,
                trials=trial_matrix(canonical),
            )
        )

    def _freeze(self, job_id: str, profile: VoiceProfile) -> VoiceProfile:
        source = Path(profile.reference.audio_path)
        target = self.path(job_id, "references", f"{profile.id}.wav")
        target.parent.mkdir(parents=True, exist_ok=True)
        if file_digest(source) != profile.reference.sha256:
            raise VoiceError("invalid_reference", "The reference changed before copying.")
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary_file:
            temporary = Path(temporary_file.name)
        try:
            _ = shutil.copy2(source, temporary)
            if (
                file_digest(temporary) != profile.reference.sha256
                or file_digest(source) != profile.reference.sha256
            ):
                raise VoiceError("invalid_reference", "The reference changed while being copied.")
            _ = temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        reference = profile.reference.model_copy(update={"audio_path": str(target)})
        return profile.model_copy(update={"reference": reference})
