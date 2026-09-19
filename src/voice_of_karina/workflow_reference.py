"""Resolve one stable voice reference before any requested message is generated."""

from pathlib import Path
from typing import assert_never

from voice_of_karina.contracts import GenerateRequest, Reference
from voice_of_karina.errors import VoiceError
from voice_of_karina.storage import Store, file_digest
from voice_of_karina.workflow_models import Adapters, JobResult
from voice_of_karina.workflow_state import has_transcripts, transition


def prepare_job(
    state: JobResult, request: GenerateRequest, store: Store, adapters: Adapters
) -> JobResult:
    """Resolve a designed, selected, or saved voice without changing established references."""
    if state.reference is not None:
        validate_reference(state.reference)
        return state
    directory = store.job_dir(state.job_id) / "reference"
    directory.mkdir(parents=True, exist_ok=True)
    state = store.save(transition(state, "preparing", "Resolving the reference voice."))
    reference: Reference
    match request.mode:
        case "reuse":
            if request.voice_id is None:
                return store.save(
                    transition(
                        state,
                        "preparing",
                        "A saved voice is required.",
                        status="needs_input",
                        input_request="Choose a saved voice_id.",
                    )
                )
            profile = store.voice(request.voice_id)
            reference = profile.reference
            state = state.model_copy(update={"voice_id": profile.id})
        case "design":
            if not request.voice_description:
                return store.save(
                    transition(
                        state,
                        "preparing",
                        "A voice description is required.",
                        status="needs_input",
                        input_request="Describe the voice you want.",
                    )
                )
            reference = adapters.design(request, directory)
        case "mimic":
            return prepare_mimic(state, request, store, adapters)
        case unreachable:
            assert_never(unreachable)
    validate_reference(reference)
    return store.save(state.model_copy(update={"reference": reference}))


def validate_reference(reference: Reference) -> None:
    """Refuse missing, changed, or textless references before allocating a model."""
    path = Path(reference.audio_path)
    if (
        not reference.transcript.strip()
        or not path.is_file()
        or file_digest(path) != reference.sha256
    ):
        raise VoiceError(
            "invalid_reference", "A matching reference WAV and transcript are required."
        )


def prepare_mimic(
    state: JobResult, request: GenerateRequest, store: Store, adapters: Adapters
) -> JobResult:
    """Select a single traceable speaker candidate, asking when identity is uncertain."""
    state = analyze_for_generation(state, request, store, adapters)
    analysis = state.analysis
    if analysis is None or not has_transcripts(analysis):
        return state
    selected_id = state.selected_candidate_id or request.candidate_id
    if selected_id is None and request.source_time is not None:
        selected_id = analysis.recommended_candidate_id
    if selected_id is None:
        unambiguous = [
            candidate for candidate in analysis.candidates if not candidate.needs_confirmation
        ]
        if len(unambiguous) != 1:
            return store.save(
                transition(
                    state,
                    "selecting",
                    "Confirm the target speaker.",
                    status="needs_selection",
                    input_request="Listen to the candidates and choose candidate_id.",
                )
            )
        selected_id = unambiguous[0].id
    selected = next(
        (candidate for candidate in analysis.candidates if candidate.id == selected_id), None
    )
    if selected is None:
        raise VoiceError("unknown_candidate", "Choose a candidate returned by this analysis.")
    state = store.save(state.model_copy(update={"selected_candidate_id": selected.id}))
    reference = adapters.prepare(selected, store.job_dir(state.job_id) / "reference")
    validate_reference(reference)
    return store.save(state.model_copy(update={"reference": reference}))


def analyze_for_generation(
    state: JobResult, request: GenerateRequest, store: Store, adapters: Adapters
) -> JobResult:
    """Reuse finished source analysis, but rerun an incomplete ASR stage on resume."""
    if state.analysis is not None and has_transcripts(state.analysis):
        return state
    if request.analysis_job_id is not None:
        previous = store.load(request.analysis_job_id)
        if previous.analysis is None:
            raise VoiceError("missing_analysis", "That job has no source analysis to reuse.")
        if request.sources and request.sources != previous.request.sources:
            raise VoiceError(
                "conflicting_sources", "Use the sources belonging to the analysis job."
            )
        analysis = previous.analysis
        if not has_transcripts(analysis):
            analysis = adapters.analyze(previous.request, store.job_dir(previous.job_id))
    else:
        if not request.sources:
            return store.save(
                transition(
                    state,
                    "analyzing",
                    "Source recordings are required.",
                    status="needs_input",
                    input_request="Provide one or more YouTube links or local recordings.",
                )
            )
        state = store.save(transition(state, "analyzing", "Finding short speech candidates."))
        analysis = adapters.analyze(request, store.job_dir(state.job_id))
    state = state.model_copy(update={"analysis": analysis})
    if not has_transcripts(analysis):
        return store.save(
            transition(
                state,
                "analyzing",
                "Reference analysis needs more information.",
                status="needs_input",
                input_request=analysis.input_request or "Provide a clearer recording.",
            )
        )
    return store.save(state)
