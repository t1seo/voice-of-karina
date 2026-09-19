from pathlib import Path
from typing import Literal, assert_never

import pytest

from tests.audition_helpers import AuditionBackend, audition_request
from voice_of_karina import audition_checks
from voice_of_karina.audition_storage import AuditionStore
from voice_of_karina.audition_workflow import AuditionWorkflow
from voice_of_karina.contracts import GeneratedAudio
from voice_of_karina.curation_contracts import SelectVoiceRequest
from voice_of_karina.errors import VoiceError
from voice_of_karina.storage import Store

type Corruption = Literal[
    "text", "settings", "reference", "model", "revision", "language", "path", "sha"
]


@pytest.mark.parametrize(
    "corruption", ["text", "settings", "reference", "model", "revision", "language", "path", "sha"]
)
def test_recovery_refuses_cross_trial_or_changed_synthesis_evidence(
    tmp_path: Path, corruption: Corruption
) -> None:
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
    audio = GeneratedAudio.model_validate_json(manifest.read_bytes())
    evidence = audio.synthesis
    assert evidence is not None
    match corruption:
        case "text":
            audio = audio.model_copy(
                update={"synthesis": evidence.model_copy(update={"text": "다른 말"})}
            )
        case "settings":
            wrong = evidence.settings.model_copy(update={"seed": 100})
            audio = audio.model_copy(
                update={"synthesis": evidence.model_copy(update={"settings": wrong})}
            )
        case "reference":
            audio = audio.model_copy(update={"reference": state.profiles[1].reference})
        case "model":
            audio = audio.model_copy(update={"model_id": "other/model"})
        case "revision":
            audio = audio.model_copy(update={"model_revision": "wrong"})
        case "language":
            audio = audio.model_copy(update={"language": "English"})
        case "path":
            other = manifest.parent / "other.wav"
            _ = other.write_bytes(Path(audio.path).read_bytes())
            audio = audio.model_copy(update={"path": str(other)})
        case "sha":
            audio = audio.model_copy(
                update={"synthesis": evidence.model_copy(update={"audio_sha256": "0" * 64})}
            )
        case unreachable:
            assert_never(unreachable)
    _ = manifest.write_text(audio.model_dump_json())
    # When
    resumed = workflow.resume(state.job_id)
    # Then
    assert resumed.trials[0].status == "failed"
    assert len(backend.calls) == 4
    assert len(resumed.choices) == 3


def test_changed_output_removes_choice_and_refuses_old_selection_hash(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path / "state")
    backend = AuditionBackend()
    workflow = AuditionWorkflow(store, backend.adapters())
    state = workflow.audition(audition_request(store, tmp_path))
    choice = state.choices[0]
    trial = next(trial for trial in state.trials if trial.id == choice.trial_ids[0])
    assert trial.audio is not None
    _ = Path(trial.audio.path).write_bytes(b"changed")
    # When
    with pytest.raises(VoiceError, match="no longer eligible"):
        _ = workflow.select(
            SelectVoiceRequest(
                job_id=state.job_id, choice_id=choice.choice_id, name="Voice", reason="Reviewed"
            )
        )
    # Then
    assert len(workflow.status(state.job_id).choices) == 3
    assert len(backend.calls) == 4


def test_changed_frozen_reference_is_refused_before_resume_can_use_it(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path / "state")
    backend = AuditionBackend()
    workflow = AuditionWorkflow(store, backend.adapters())
    state = workflow.audition(audition_request(store, tmp_path))
    _ = Path(state.profiles[0].reference.audio_path).write_bytes(b"changed")
    # When
    with pytest.raises(VoiceError, match="reference is missing or changed"):
        _ = workflow.resume(state.job_id)
    # Then
    assert len(backend.calls) == 4


def test_status_holds_stale_policy_without_revalidating_or_reusing_old_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    store = Store(tmp_path / "state")
    backend = AuditionBackend()
    workflow = AuditionWorkflow(store, backend.adapters())
    state = workflow.audition(audition_request(store, tmp_path))
    monkeypatch.setattr(audition_checks, "QUALITY_POLICY_VERSION", "future-policy")
    before = len(backend.validations)
    # When
    checked = workflow.status(state.job_id)
    # Then
    assert checked.status == "needs_input"
    assert checked.choices == ()
    assert len(backend.validations) == before


def test_selected_profile_must_match_choice_reference_and_recipe(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path / "state")
    storage = AuditionStore(store)
    workflow = AuditionWorkflow(store, AuditionBackend().adapters())
    state = workflow.audition(audition_request(store, tmp_path))
    choice = state.choices[0]
    _ = workflow.select(
        SelectVoiceRequest(
            job_id=state.job_id, choice_id=choice.choice_id, name="Voice", reason="Reviewed"
        )
    )
    selected = storage.load(state.job_id)
    other = next(profile for profile in state.profiles if profile.id != choice.voice_id)
    wrong = store.save_voice(
        "Wrong", other.reference, recipe=choice.recipe, selection=selected.selection
    )
    _ = storage.save(selected.model_copy(update={"selected_voice_id": wrong.id}))
    # When
    with pytest.raises(VoiceError, match="different selection"):
        _ = workflow.status(state.job_id)
