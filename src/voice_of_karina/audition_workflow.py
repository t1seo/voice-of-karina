"""Public comparison workflow: bounded previews, explicit choice, reusable recipe."""

from dataclasses import dataclass

from voice_of_karina.audition_checks import profile_for, refresh
from voice_of_karina.audition_execution import run_trials
from voice_of_karina.audition_models import AuditionChoice, AuditionResult
from voice_of_karina.audition_storage import AuditionStore
from voice_of_karina.curation_contracts import AuditionRequest, ProfileResult, SelectVoiceRequest
from voice_of_karina.errors import VoiceError
from voice_of_karina.storage import Store
from voice_of_karina.workflow_models import Adapters, VoiceSelection


def _choice(state: AuditionResult, choice_id: str) -> AuditionChoice:
    for choice in state.choices:
        if choice.choice_id == choice_id:
            return choice
    raise VoiceError(
        "stale_choice", "The selected preview is no longer eligible; review current choices."
    )


def _finalize(state: AuditionResult, storage: AuditionStore) -> AuditionResult:
    selection = state.selection
    name = state.selection_name
    if selection is None or name is None:
        raise VoiceError("missing_selection", "Choose a preview and describe the review first.")
    choice = _choice(state, selection.choice_id)
    if state.selected_voice_id is not None:
        return state
    profile = storage.store.save_voice(
        name,
        profile_for(state, choice.voice_id).reference,
        recipe=choice.recipe,
        selection=selection,
    )
    return storage.save(
        state.model_copy(update={"selected_voice_id": profile.id, "status": "complete"})
    )


@dataclass(frozen=True, slots=True)
class AuditionWorkflow:
    """Serialize the full comparison or selection transaction with the shared job lock."""

    store: Store
    adapters: Adapters

    def audition(self, request: AuditionRequest) -> AuditionResult:
        """Identical paired comparisons share their original finite trial ledger."""
        storage = AuditionStore(self.store)
        job_id = storage.identity(request)
        _ = storage.path(job_id)
        with self.store.lock(job_id):
            state = storage.load(job_id) if storage.exists(job_id) else storage.initialize(request)
            return self._continue(state, storage)

    def status(self, job_id: str) -> AuditionResult:
        """Read current integrity and policy eligibility without invoking a model."""
        storage = AuditionStore(self.store)
        return refresh(storage.load(job_id), storage)

    def resume(self, job_id: str) -> AuditionResult:
        """Recover consumed work and run only cells that have never started."""
        storage = AuditionStore(self.store)
        _ = storage.load(job_id)
        with self.store.lock(job_id):
            return self._continue(storage.load(job_id), storage)

    def _continue(self, state: AuditionResult, storage: AuditionStore) -> AuditionResult:
        checked = run_trials(state, storage, self.adapters)
        return _finalize(checked, storage) if checked.selection is not None else checked

    def select(self, request: SelectVoiceRequest) -> ProfileResult:
        """Persist the explicit choice before copying a durable profile; never auto-rank."""
        storage = AuditionStore(self.store)
        _ = storage.load(request.job_id)
        with self.store.lock(request.job_id):
            state = storage.load(request.job_id)
            if state.selection is not None and state.selection.choice_id != request.choice_id:
                raise VoiceError(
                    "selection_finalized", "This audition already selected another choice."
                )
            if any(trial.attempts == 0 or trial.status == "started" for trial in state.trials):
                raise VoiceError(
                    "audition_incomplete", "Resume the remaining comparison trials first."
                )
            state = storage.save(refresh(state, storage, self.adapters))
            _ = _choice(state, request.choice_id)
            if state.selection is None:
                state = storage.save(
                    state.model_copy(
                        update={
                            "selection": VoiceSelection(
                                audition_id=state.job_id,
                                choice_id=request.choice_id,
                                reason=request.reason,
                            ),
                            "selection_name": request.name,
                        }
                    )
                )
            completed = _finalize(state, storage)
            if completed.selected_voice_id is None:
                raise VoiceError("missing_selection", "The selected voice was not saved.")
            return ProfileResult(voice=self.store.voice(completed.selected_voice_id))
