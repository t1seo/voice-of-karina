import shutil
from pathlib import Path

import pytest

from tests.audition_helpers import AuditionBackend, audition_request
from voice_of_karina.audition_storage import AuditionStore
from voice_of_karina.audition_workflow import AuditionWorkflow
from voice_of_karina.contracts import Message, Reference
from voice_of_karina.curation_contracts import SelectVoiceRequest
from voice_of_karina.errors import VoiceError
from voice_of_karina.generation_settings import VoiceRecipe
from voice_of_karina.storage import Store
from voice_of_karina.workflow_models import VoiceProfile, VoiceSelection


def test_audition_directory_alias_is_refused_before_any_files_are_written(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path / "state")
    request = audition_request(store, tmp_path)
    real = store.job_dir("audition-real")
    real.mkdir(parents=True)
    alias = store.job_dir(AuditionStore(store).identity(request))
    alias.symlink_to(real, target_is_directory=True)
    # When
    with pytest.raises(VoiceError, match="aliases"):
        _ = AuditionWorkflow(store, AuditionBackend().adapters()).audition(request)
    # Then
    assert tuple(real.iterdir()) == ()


def test_symlinked_recovery_manifest_cannot_alias_another_artifact(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path / "state")
    storage = AuditionStore(store)
    request = audition_request(store, tmp_path)
    backend = AuditionBackend(crash_at=1, write_before_crash=True)
    workflow = AuditionWorkflow(store, backend.adapters())
    with pytest.raises(KeyboardInterrupt):
        _ = workflow.audition(request)
    state = storage.load(storage.identity(request))
    trial = state.trials[0]
    manifest = storage.path(state.job_id, "trials", trial.id, f"{trial.message.id}.audio.json")
    other = tmp_path / "aliased.json"
    _ = manifest.replace(other)
    manifest.symlink_to(other)
    # When
    resumed = workflow.resume(state.job_id)
    # Then
    assert resumed.trials[0].status == "failed"
    assert len(backend.calls) == 4


def test_interruption_after_profile_save_replays_the_persisted_selection_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    store = Store(tmp_path / "state")
    storage = AuditionStore(store)
    workflow = AuditionWorkflow(store, AuditionBackend().adapters())
    state = workflow.audition(audition_request(store, tmp_path))
    selection_request = SelectVoiceRequest(
        job_id=state.job_id,
        choice_id=state.choices[0].choice_id,
        name="Original name",
        reason="Automatic checks passed; listening is pending.",
    )
    original = store.save_voice
    saved_ids: list[str] = []

    def interrupted(
        name: str,
        reference: Reference,
        *,
        recipe: VoiceRecipe | None = None,
        selection: VoiceSelection | None = None,
    ) -> VoiceProfile:
        saved_ids.append(original(name, reference, recipe=recipe, selection=selection).id)
        raise KeyboardInterrupt

    monkeypatch.setattr(store, "save_voice", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _ = workflow.select(selection_request)
    intent = storage.load(state.job_id)
    assert intent.selection is not None
    assert intent.selected_voice_id is None
    monkeypatch.setattr(store, "save_voice", original)
    # When
    resumed = workflow.resume(state.job_id)
    # Then
    assert resumed.selected_voice_id == saved_ids[0]
    assert resumed.selection_name == selection_request.name
    assert resumed.selection is not None
    assert resumed.selection.reason == selection_request.reason
    assert resumed.status == "complete"
    assert len(store.voices()) == 3


def test_largest_matrix_consumes_twelve_cells_and_reordered_texts_share_it(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path / "state")
    request = audition_request(store, tmp_path, count=3).model_copy(
        update={
            "messages": (
                Message(id="first", text="첫째 문장."),
                Message(id="second", text="다른 문장."),
            )
        }
    )
    backend = AuditionBackend()
    workflow = AuditionWorkflow(store, backend.adapters())
    state = workflow.audition(request)
    reordered = request.model_copy(update={"messages": tuple(reversed(request.messages))})
    # When
    replay = workflow.audition(reordered)
    # Then
    assert replay == state
    assert len(backend.calls) == 12
    assert len(replay.choices) == 6
    assert all(len(choice.trial_ids) == 2 for choice in replay.choices)


def test_reference_mutation_during_freezing_cannot_start_any_synthesis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    store = Store(tmp_path / "state")
    request = audition_request(store, tmp_path)
    backend = AuditionBackend()
    original = shutil.copy2

    def copy_then_change(source: Path, target: Path) -> Path:
        _ = original(source, target)
        _ = source.write_bytes(b"changed during copy")
        return target

    monkeypatch.setattr(shutil, "copy2", copy_then_change)
    # When
    with pytest.raises(VoiceError, match="changed while being copied"):
        _ = AuditionWorkflow(store, backend.adapters()).audition(request)
    # Then
    assert backend.calls == []
    assert not AuditionStore(store).exists(AuditionStore(store).identity(request))
