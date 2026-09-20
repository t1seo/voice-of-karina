from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.test_reference_refinement import (
    original_transcript,
    parent_candidate,
    verified_refinement,
    write_reference,
)
from voice_of_karina import audio, audio_transcription
from voice_of_karina.errors import VoiceError
from voice_of_karina.sources import file_sha256

if TYPE_CHECKING:
    from voice_of_karina.backends.stt import Transcript


@pytest.mark.parametrize("removed", [False, True])
def test_prepare_rejects_changed_candidate_instead_of_rebinding_old_transcript(
    tmp_path: Path,
    removed: bool,
) -> None:
    # Given a verified transcript bound to a candidate that was subsequently replaced.
    path = write_reference(tmp_path / "candidate.wav")
    candidate = parent_candidate(path).model_copy(
        update={"transcript": original_transcript().text, "sha256": file_sha256(path)}
    )
    if removed:
        path.unlink()
    else:
        _ = write_reference(path, continuous=True)
    # When the old selection is prepared for synthesis.
    with pytest.raises(VoiceError) as raised:
        _ = audio.prepare_reference(candidate, tmp_path / "job")
    # Then the changed recording cannot inherit the old transcript.
    assert raised.value.code == "changed_candidate"


def test_prepare_rejects_copy_that_changes_after_initial_integrity_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a candidate whose copy is changed during the copy operation.
    path = write_reference(tmp_path / "candidate.wav")
    candidate = parent_candidate(path).model_copy(
        update={"transcript": original_transcript().text, "sha256": file_sha256(path)}
    )
    original_copy = shutil.copyfile

    def changed_copy(source: Path, destination: Path) -> str:
        _ = original_copy(source, destination)
        _ = destination.write_bytes(b"changed during copy")
        return str(destination)

    monkeypatch.setattr(shutil, "copyfile", changed_copy)
    # When reference persistence runs.
    with pytest.raises(VoiceError) as raised:
        _ = audio.prepare_reference(candidate, tmp_path / "job")
    # Then the corrupted copy is never accepted as the reference.
    assert raised.value.code == "changed_candidate"
    assert not tuple((tmp_path / "job" / "references").glob("*.wav"))


def test_transcribed_originals_and_shorter_candidates_bind_their_actual_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given original and independently checked shorter native-rate references.
    # When both ASR stages finish.
    result = verified_refinement(tmp_path, monkeypatch)
    # Then every returned transcript is paired with the digest of its own WAV.
    assert all(
        candidate.sha256 == file_sha256(Path(candidate.audio_path))
        for candidate in result.candidates
    )


def test_transcription_rejects_native_bytes_changed_while_asr_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given ASR that returns words after the corresponding source has changed.
    path = write_reference(tmp_path / "candidate.wav")
    candidate = parent_candidate(path).model_copy(update={"sha256": file_sha256(path)})

    def recognize(
        paths: tuple[Path, ...], *, language: str | None = "ko"
    ) -> tuple[Transcript, ...]:
        assert len(paths) == 1
        assert language == "ko"
        _ = write_reference(path, continuous=True)
        return (original_transcript(),)

    monkeypatch.setattr(audio_transcription, "transcribe_many", recognize)
    # When candidate transcription runs.
    candidates, warning = audio_transcription.transcribe_candidates((candidate,), tmp_path / "job")
    # Then changed bytes are not assigned the completed ASR result.
    assert candidates[0].transcript is None
    assert warning is not None
    assert "changed" in warning.lower()
