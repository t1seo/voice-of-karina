"""Atomic, locked storage for jobs and copied reusable voice references."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from voice_of_karina.contracts import GenerateRequest
from voice_of_karina.errors import VoiceError
from voice_of_karina.workflow_models import JobResult, VoiceProfile, VoiceSelection

if TYPE_CHECKING:
    from collections.abc import Generator

    from voice_of_karina.contracts import AnalyzeRequest, FrozenModel, Reference
    from voice_of_karina.generation_settings import VoiceRecipe


def file_digest(path: Path) -> str:
    """Hash file contents without holding a recording in memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_id(value: str) -> str:
    """Reject identifiers that could escape a job or profile directory."""
    if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", value) is None:
        raise VoiceError("invalid_id", "Use the job or voice identifier returned by the engine.")
    return value


def profile_identity(
    reference: Reference, recipe: VoiceRecipe | None, selection: VoiceSelection | None
) -> str:
    """Keep legacy IDs stable while binding new recipes and review evidence."""
    digest = hashlib.sha256(
        reference.model_copy(update={"audio_path": ""}).model_dump_json().encode()
    )
    if recipe is not None:
        digest.update(b"\nvoice-recipe-v1\n" + recipe.model_dump_json().encode())
    if selection is not None:
        digest.update(b"\nvoice-selection-v1\n" + selection.model_dump_json().encode())
    return "voice-" + digest.hexdigest()[:24]


class Store:
    """Own all job writes; filesystem locks serialize duplicate invocations."""

    root: Path
    cache_key: str

    def __init__(self, root: Path | None = None, *, cache_key: str = "voice-of-karina-v2") -> None:
        """Resolve an explicit or environment-configured storage location."""
        configured = os.environ.get("VOICE_OF_KARINA_HOME")
        self.root = (root or Path(configured or ".voice-of-karina")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache_key = cache_key

    def job_dir(self, job_id: str) -> Path:
        """Return a contained path even when a hostile symlink exists."""
        path = self.root / "jobs" / safe_id(job_id)
        if not path.resolve().is_relative_to(self.root):
            raise VoiceError("invalid_path", "The job directory escapes the voice store.")
        return path

    def request_id(self, request: AnalyzeRequest | GenerateRequest) -> str:
        """Identical requests share one attempt budget and lock."""
        sources = tuple(
            source if "://" in source else str(Path(source).expanduser().resolve())
            for source in request.sources
        )
        canonical = request.model_copy(update={"sources": sources})
        excluded: set[str] = (
            {"settings"}
            if isinstance(canonical, GenerateRequest) and canonical.settings is None
            else set()
        )
        digest = hashlib.sha256(
            self.cache_key.encode() + canonical.model_dump_json(exclude=excluded).encode()
        )
        for source in sources:
            path = Path(source)
            if "://" not in source and path.is_file():
                digest.update(file_digest(path).encode())
        return "job-" + digest.hexdigest()[:24]

    @contextmanager
    def lock(self, job_id: str) -> Generator[None]:
        """Hold a nonblocking process lock for a full state-changing operation."""
        if sys.platform == "win32":
            raise VoiceError("unsupported_platform", "Job execution requires macOS or Linux.")
        import fcntl

        directory = self.job_dir(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / ".lock").open("a") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise VoiceError(
                    "job_busy", "This job is already running; read its status."
                ) from error
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def exists(self, job_id: str) -> bool:
        """Distinguish a saved job from a lock-only directory."""
        return (self.job_dir(job_id) / "job.json").is_file()

    def load(self, job_id: str) -> JobResult:
        """Read typed persisted state; never create a phantom job."""
        try:
            state = JobResult.model_validate_json((self.job_dir(job_id) / "job.json").read_bytes())
        except FileNotFoundError as error:
            raise VoiceError("unknown_job", "No saved job has this identifier.") from error
        except ValidationError as error:
            raise VoiceError("invalid_job", "Saved job metadata is damaged.") from error
        if state.job_id != job_id:
            raise VoiceError("invalid_job", "Saved job identifier does not match its directory.")
        return state

    def save(self, state: JobResult) -> JobResult:
        """Persist one complete state snapshot atomically."""
        self.atomic_write(self.job_dir(state.job_id) / "job.json", state)
        return state

    def atomic_write(self, path: Path, value: FrozenModel) -> None:
        """Replace complete UTF-8 JSON only after flushing the temporary file."""
        if not path.resolve().is_relative_to(self.root):
            raise VoiceError("invalid_path", "The state path escapes the voice store.")
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as f:
            temporary = Path(f.name)
            try:
                _ = f.write(value.model_dump_json(indent=2))
                f.flush()
                os.fsync(f.fileno())
            except OSError:
                temporary.unlink(missing_ok=True)
                raise
        try:
            _ = temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def save_voice(
        self,
        name: str,
        reference: Reference,
        *,
        recipe: VoiceRecipe | None = None,
        selection: VoiceSelection | None = None,
    ) -> VoiceProfile:
        """Copy the reference so a reusable voice is independent of job outputs."""
        voice_id = profile_identity(reference, recipe, selection)
        with self.lock(voice_id):
            return self._save_voice(name, reference, recipe=recipe, selection=selection)

    def _save_voice(
        self,
        name: str,
        reference: Reference,
        *,
        recipe: VoiceRecipe | None,
        selection: VoiceSelection | None,
    ) -> VoiceProfile:
        voice_id = profile_identity(reference, recipe, selection)
        directory = self.root / "voices" / voice_id
        if not directory.resolve().is_relative_to(self.root):
            raise VoiceError("invalid_path", "The profile directory escapes the voice store.")
        directory.mkdir(parents=True, exist_ok=True)
        if (directory / "profile.json").is_file():
            return self.voice(voice_id)
        original = Path(reference.audio_path)
        if not original.is_file() or file_digest(original) != reference.sha256:
            raise VoiceError("invalid_reference", "The reference audio is missing or changed.")
        target = directory / "reference.wav"
        if not target.resolve().is_relative_to(directory.resolve()):
            raise VoiceError("invalid_path", "The profile reference escapes its directory.")
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary_file:
            temporary = Path(temporary_file.name)
        try:
            _ = shutil.copy2(original, temporary)
            if file_digest(temporary) != reference.sha256:
                raise VoiceError("invalid_reference", "The reference changed during copying.")
            _ = temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        profile = VoiceProfile(
            id=voice_id,
            name=name,
            reference=reference.model_copy(update={"audio_path": str(target)}),
            created_at=datetime.now(UTC).isoformat(),
            recipe=recipe,
            selection=selection,
        )
        self.atomic_write(directory / "profile.json", profile)
        return profile

    def voice(self, voice_id: str) -> VoiceProfile:
        """Load a profile only when its immutable audio still matches its digest."""
        path = self.root / "voices" / safe_id(voice_id) / "profile.json"
        if not path.resolve().is_relative_to(self.root):
            raise VoiceError("invalid_path", "The profile directory escapes the voice store.")
        try:
            profile = VoiceProfile.model_validate_json(path.read_bytes())
        except FileNotFoundError as error:
            raise VoiceError("unknown_voice", "No saved voice has this identifier.") from error
        except ValidationError as error:
            raise VoiceError("invalid_voice", "Saved voice metadata is damaged.") from error
        audio = Path(profile.reference.audio_path)
        if profile.id != voice_id or not audio.resolve().is_relative_to(path.parent.resolve()):
            raise VoiceError("invalid_profile", "Saved voice paths do not match their profile.")
        if profile_identity(profile.reference, profile.recipe, profile.selection) != voice_id:
            raise VoiceError("invalid_profile", "Saved voice identity does not match its recipe.")
        if not audio.is_file() or file_digest(audio) != profile.reference.sha256:
            raise VoiceError(
                "invalid_reference", "The saved voice reference is missing or changed."
            )
        return profile

    def voices(self) -> tuple[VoiceProfile, ...]:
        """List profiles after verifying their copied reference files."""
        return tuple(
            self.voice(path.parent.name)
            for path in sorted((self.root / "voices").glob("*/profile.json"))
        )
