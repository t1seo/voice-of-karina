"""Skill-facing orchestration with durable progress and bounded resumption."""

from dataclasses import dataclass
from typing import assert_never

from voice_of_karina.contracts import AnalyzeRequest, GenerateRequest, RejectRequest, ResumeRequest
from voice_of_karina.errors import VoiceError
from voice_of_karina.storage import Store
from voice_of_karina.workflow_generation import run_generation
from voice_of_karina.workflow_models import (
    Adapters,
    ErrorDetail,
    JobResult,
    MessageResult,
    VoicesResult,
)
from voice_of_karina.workflow_reference import prepare_job
from voice_of_karina.workflow_review import reject_output
from voice_of_karina.workflow_state import has_transcripts, integrity, transition


@dataclass(frozen=True, slots=True)
class Workflow:
    """Own state transitions; adapters own source analysis and model execution."""

    store: Store
    adapters: Adapters

    def analyze(self, request: AnalyzeRequest) -> JobResult:
        """Create or resume an analysis under its shared process lock."""
        job_id = self.store.request_id(request)
        with self.store.lock(job_id):
            state = (
                self.store.load(job_id)
                if self.store.exists(job_id)
                else JobResult(job_id=job_id, request=request)
            )
            return self.continue_analysis(state, request)

    def continue_analysis(self, state: JobResult, request: AnalyzeRequest) -> JobResult:
        """Preserve candidate uncertainty and retry previously incomplete analysis."""
        if not request.sources:
            return self.store.save(
                transition(
                    state,
                    "analyzing",
                    "Source recordings are required.",
                    status="needs_input",
                    input_request="Provide one or more YouTube links or local recordings.",
                )
            )
        state = self.store.save(
            transition(state, "analyzing", "Analyzing the supplied recordings.")
        )
        try:
            analysis = state.analysis
            if analysis is None or not has_transcripts(analysis):
                analysis = self.adapters.analyze(request, self.store.job_dir(state.job_id))
        except VoiceError as error:
            return self.failure(state, error)
        state = state.model_copy(update={"analysis": analysis})
        if not has_transcripts(analysis):
            return self.store.save(
                transition(
                    state,
                    "analyzing",
                    "No usable speech was found.",
                    status="needs_input",
                    input_request=analysis.input_request or "Provide a clearer recording.",
                )
            )
        needs_selection = any(candidate.needs_confirmation for candidate in analysis.candidates)
        return self.store.save(
            transition(
                state,
                "analyzed",
                "Speech candidates are ready.",
                status="needs_selection" if needs_selection else "complete",
                input_request="Listen and choose the target speaker's candidate_id."
                if needs_selection
                else None,
            )
        )

    def generate(self, request: GenerateRequest) -> JobResult:
        """Share attempt budgets when an identical request is submitted twice."""
        job_id = self.store.request_id(request)
        with self.store.lock(job_id):
            if self.store.exists(job_id):
                state = self.store.load(job_id)
            else:
                state = self.store.save(
                    JobResult(
                        job_id=job_id,
                        request=request,
                        messages=tuple(
                            MessageResult(message_id=message.id, text=message.text)
                            for message in request.messages
                        ),
                    )
                )
            return self.continue_generation(state, request)

    def resume(self, request: ResumeRequest) -> JobResult:
        """Continue the saved request without resetting its attempts or reference."""
        _ = self.store.load(request.job_id)
        with self.store.lock(request.job_id):
            state = self.store.load(request.job_id)
            if request.candidate_id is not None:
                selected = state.selected_candidate_id
                if selected is not None and request.candidate_id != selected:
                    raise VoiceError(
                        "conflicting_selection",
                        "Start a new request to change an established voice.",
                    )
                state = state.model_copy(update={"selected_candidate_id": request.candidate_id})
            original = state.request
            match original:
                case AnalyzeRequest():
                    return self.continue_analysis(state, original)
                case GenerateRequest():
                    return self.continue_generation(state, original)
                case unreachable:
                    assert_never(unreachable)

    def status(self, job_id: str) -> JobResult:
        """Read status while checking accepted files without changing saved state."""
        return integrity(self.store.load(job_id), self.store)

    def reject(self, request: RejectRequest) -> JobResult:
        """Serialize review with generation while retaining the original attempt budget."""
        _ = self.store.load(request.job_id)
        with self.store.lock(request.job_id):
            return reject_output(self.store.load(request.job_id), request, self.store)

    def voices(self) -> VoicesResult:
        """Expose validated saved voices to the conversation agent."""
        return VoicesResult(voices=self.store.voices())

    def continue_generation(self, state: JobResult, request: GenerateRequest) -> JobResult:
        """Keep reference preparation, bounded attempts, and profile persistence durable."""
        state = self.store.save(integrity(state, self.store))
        try:
            state = prepare_job(state, request, self.store, self.adapters)
            if state.reference is None:
                return state
            reference = state.reference
            state = run_generation(state, request, self.store, self.adapters)
            if request.profile_name is not None and state.voice_id is None:
                profile = self.store.save_voice(request.profile_name, reference)
                state = self.store.save(state.model_copy(update={"voice_id": profile.id}))
        except VoiceError as error:
            return self.failure(state, error)
        return state

    def failure(self, state: JobResult, error: VoiceError) -> JobResult:
        """Retain a typed failure without deleting prior outputs."""
        failed = transition(state, state.step, error.message, status="failed")
        return self.store.save(
            failed.model_copy(
                update={
                    "errors": (*state.errors, ErrorDetail(code=error.code, message=error.message))
                }
            )
        )
