import errno
import os
from pathlib import Path

import pytest

from voice_of_karina.contracts import AnalyzeRequest, Provenance, Reference
from voice_of_karina.errors import VoiceError
from voice_of_karina.storage import Store, file_digest
from voice_of_karina.workflow_models import JobResult


@pytest.fixture
def voice_reference(tmp_path: Path) -> Reference:
    source = tmp_path / "source.wav"
    _ = source.write_bytes(b"source recording")
    return Reference(
        audio_path=str(source),
        sha256=file_digest(source),
        transcript="reference words",
        provenance=Provenance(kind="design", description="calm"),
        model_id="test-model",
        model_revision="test-revision",
    )


def test_saved_voice_when_source_removed_keeps_copied_reference(
    tmp_path: Path, voice_reference: Reference
) -> None:
    # Given
    store = Store(tmp_path / "store")
    profile = store.save_voice("Calm", voice_reference)
    # When
    Path(voice_reference.audio_path).unlink()
    # Then
    assert store.voices() == (profile,)
    copied = store.voice(profile.id).reference
    assert Path(copied.audio_path).read_bytes() == b"source recording"
    assert copied.model_copy(update={"audio_path": voice_reference.audio_path}) == voice_reference


def test_saved_voice_when_copy_changes_is_rejected(
    tmp_path: Path, voice_reference: Reference
) -> None:
    # Given
    store = Store(tmp_path / "store")
    profile = store.save_voice("Calm", voice_reference)
    _ = Path(profile.reference.audio_path).write_bytes(b"different recording")
    # When / Then
    with pytest.raises(VoiceError, match="saved voice reference is missing or changed"):
        _ = store.voices()


def test_atomic_save_when_sync_fails_preserves_previous_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    store = Store(tmp_path)
    previous = JobResult(job_id="job-a", request=AnalyzeRequest(), status="needs_input")
    _ = store.save(previous)
    updated = JobResult(job_id="job-a", request=AnalyzeRequest(), status="needs_selection")

    def failing_sync(_descriptor: int) -> None:
        raise OSError(errno.EIO, "simulated sync failure")

    monkeypatch.setattr(os, "fsync", failing_sync)
    # When
    with pytest.raises(OSError, match="simulated sync failure"):
        _ = store.save(updated)
    # Then
    assert store.load(previous.job_id) == previous
    assert {path.name for path in store.job_dir(previous.job_id).iterdir()} == {"job.json"}
