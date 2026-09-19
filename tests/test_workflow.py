from pathlib import Path

import pytest

from voice_of_karina.contracts import (
    AnalysisResult,
    AnalyzeRequest,
    Candidate,
    GeneratedAudio,
    GenerateRequest,
    Message,
    Provenance,
    QualityOptions,
    QualityResult,
    Reference,
    ResumeRequest,
)
from voice_of_karina.quality_policy import QUALITY_POLICY_VERSION
from voice_of_karina.storage import Store, file_digest
from voice_of_karina.workflow import Workflow
from voice_of_karina.workflow_models import Adapters


class ModelBoundary:
    calls: int
    failures: int
    interrupted: bool
    seen: list[tuple[str, ...]]

    def __init__(self, *, failures: int = 0, interrupted: bool = False) -> None:
        self.calls = 0
        self.failures = failures
        self.interrupted = interrupted
        self.seen = []

    def analyze(
        self, _request: AnalyzeRequest | GenerateRequest, _directory: Path
    ) -> AnalysisResult:
        pytest.fail("reuse/design must bypass analysis")

    def prepare(self, _candidate: Candidate, _directory: Path) -> Reference:
        pytest.fail("reuse/design must bypass source preparation")

    def design(self, request: GenerateRequest, directory: Path) -> Reference:
        path = directory / "reference.wav"
        directory.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(b"reference")
        return Reference(
            audio_path=str(path),
            sha256=file_digest(path),
            transcript="reference",
            provenance=Provenance(kind="design", description=request.voice_description),
        )

    def synthesize(
        self, request: GenerateRequest, _reference: Reference | None, directory: Path
    ) -> list[GeneratedAudio]:
        self.calls += 1
        self.seen.append(tuple(message.id for message in request.messages))
        if self.interrupted:
            self.interrupted = False
            raise KeyboardInterrupt
        directory.mkdir(parents=True, exist_ok=True)
        result: list[GeneratedAudio] = []
        for message in request.messages:
            path = directory / f"{message.id}.wav"
            _ = path.write_bytes(
                b"bad" if self.calls <= self.failures and message.id == "second" else b"good"
            )
            result.append(
                GeneratedAudio(
                    message_id=message.id,
                    path=str(path),
                    sample_rate=24000,
                    duration_seconds=1,
                    model_id="test-boundary",
                    elapsed_seconds=0,
                )
            )
        return result

    def validate(
        self, audio: GeneratedAudio, _expected: str, _options: QualityOptions
    ) -> QualityResult:
        accepted = Path(audio.path).read_bytes() == b"good"
        return QualityResult(
            valid=accepted,
            decision="pass" if accepted else "retry",
            policy_version=QUALITY_POLICY_VERSION,
        )

    def adapters(self) -> Adapters:
        return Adapters(self.analyze, self.prepare, self.design, self.synthesize, self.validate)


def request(*, attempts: int = 2) -> GenerateRequest:
    return GenerateRequest(
        mode="design",
        voice_description="calm",
        max_attempts=attempts,
        profile_name="Calm",
        messages=(Message(id="first", text="one"), Message(id="second", text="two")),
    )


def test_failed_line_when_retried_preserves_successful_line(tmp_path: Path) -> None:
    # Given
    boundary = ModelBoundary(failures=1)
    workflow = Workflow(Store(tmp_path), boundary.adapters())
    # When
    result = workflow.generate(request())
    # Then
    assert result.status == "complete"
    assert boundary.seen == [("first", "second"), ("second",)]
    assert [line.attempts for line in result.messages] == [1, 2]


def test_exhausted_retries_when_resumed_do_not_reset_budget(tmp_path: Path) -> None:
    # Given
    boundary = ModelBoundary(failures=100)
    workflow = Workflow(Store(tmp_path), boundary.adapters())
    original = workflow.generate(request())
    # When
    resumed = workflow.resume(ResumeRequest(job_id=original.job_id))
    # Then
    assert resumed.status == "partial"
    assert boundary.calls == 2
    assert [line.attempts for line in resumed.messages] == [1, 2]


def test_interruption_when_resumed_consumes_persisted_attempt(tmp_path: Path) -> None:
    # Given
    boundary = ModelBoundary(interrupted=True)
    store = Store(tmp_path)
    workflow = Workflow(store, boundary.adapters())
    with pytest.raises(KeyboardInterrupt):
        _ = workflow.generate(request())
    saved = store.load(store.request_id(request()))
    assert [line.attempts for line in saved.messages] == [1, 1]
    # When
    result = workflow.resume(ResumeRequest(job_id=saved.job_id))
    # Then
    assert result.status == "complete"
    assert [line.attempts for line in result.messages] == [2, 2]


def test_changed_output_when_resumed_regenerates_only_affected_line(tmp_path: Path) -> None:
    # Given
    boundary = ModelBoundary()
    workflow = Workflow(Store(tmp_path), boundary.adapters())
    original = workflow.generate(request())
    audio = original.messages[1].audio
    assert audio is not None
    _ = Path(audio.path).write_bytes(b"corrupt")
    # When
    result = workflow.resume(ResumeRequest(job_id=original.job_id))
    # Then
    assert result.status == "complete"
    assert boundary.seen == [("first", "second"), ("second",)]


def test_identical_request_when_repeated_reuses_verified_outputs(tmp_path: Path) -> None:
    # Given
    boundary = ModelBoundary()
    workflow = Workflow(Store(tmp_path), boundary.adapters())
    first = workflow.generate(request())
    # When
    second = workflow.generate(request())
    # Then
    assert first.job_id == second.job_id
    assert second.status == "complete"
    assert boundary.calls == 1


def test_analysis_when_transcribed_but_ambiguous_requests_selection(tmp_path: Path) -> None:
    # Given
    boundary = ModelBoundary()
    candidate = Candidate(
        id="clip",
        source_id="source",
        audio_path=str(tmp_path / "clip.wav"),
        start_seconds=0,
        end_seconds=5,
        transcript="hello",
        needs_confirmation=True,
    )
    analysis = AnalysisResult(candidates=(candidate,), input_request="Choose the speaker.")
    adapters = boundary.adapters()

    def analyze(_request: AnalyzeRequest | GenerateRequest, _directory: Path) -> AnalysisResult:
        return analysis

    workflow = Workflow(
        Store(tmp_path),
        Adapters(
            analyze, adapters.prepare, adapters.design, adapters.synthesize, adapters.validate
        ),
    )
    # When
    result = workflow.analyze(AnalyzeRequest(sources=("source.wav",)))
    # Then
    assert result.status == "needs_selection"
    assert boundary.calls == 0


def test_mimic_when_candidate_selected_ignores_selection_prompt(tmp_path: Path) -> None:
    # Given
    boundary = ModelBoundary()
    adapters = boundary.adapters()
    path = tmp_path / "clip.wav"
    _ = path.write_bytes(b"reference")
    candidate = Candidate(
        id="clip",
        source_id="source",
        audio_path=str(path),
        start_seconds=0,
        end_seconds=5,
        transcript="hello",
        needs_confirmation=True,
    )

    def analyze(_request: AnalyzeRequest | GenerateRequest, _directory: Path) -> AnalysisResult:
        return AnalysisResult(candidates=(candidate,), input_request="Choose the speaker.")

    def prepare(selected: Candidate, _directory: Path) -> Reference:
        return Reference(
            audio_path=selected.audio_path,
            sha256=file_digest(path),
            transcript="hello",
            provenance=Provenance(kind="mimic", source="source.wav"),
        )

    workflow = Workflow(
        Store(tmp_path / "store"),
        Adapters(analyze, prepare, adapters.design, adapters.synthesize, adapters.validate),
    )
    # When
    result = workflow.generate(
        GenerateRequest(
            mode="mimic",
            sources=(str(path),),
            candidate_id="clip",
            messages=(Message(id="first", text="one"),),
        )
    )
    # Then
    assert result.status == "complete"
    assert result.selected_candidate_id == "clip"


def test_analysis_when_transcript_unavailable_stays_needs_input(tmp_path: Path) -> None:
    # Given
    boundary = ModelBoundary()
    adapters = boundary.adapters()
    candidate = Candidate(
        id="clip",
        source_id="source",
        audio_path=str(tmp_path / "clip.wav"),
        start_seconds=0,
        end_seconds=5,
        needs_confirmation=False,
    )

    def analyze(_request: AnalyzeRequest | GenerateRequest, _directory: Path) -> AnalysisResult:
        return AnalysisResult(candidates=(candidate,), input_request="ASR is unavailable.")

    workflow = Workflow(
        Store(tmp_path),
        Adapters(
            analyze, adapters.prepare, adapters.design, adapters.synthesize, adapters.validate
        ),
    )
    # When
    result = workflow.analyze(AnalyzeRequest(sources=("source.wav",)))
    # Then
    assert result.status == "needs_input"


def test_saved_voice_when_reused_skips_design_and_source_analysis(tmp_path: Path) -> None:
    # Given
    boundary = ModelBoundary()
    store = Store(tmp_path)
    original = Workflow(store, boundary.adapters()).generate(request())
    assert original.voice_id is not None
    adapters = boundary.adapters()
    reuse = Workflow(
        store,
        Adapters(
            adapters.analyze,
            adapters.prepare,
            lambda _request, _directory: pytest.fail("Reuse cannot redesign a voice"),
            adapters.synthesize,
            adapters.validate,
        ),
    )
    # When
    result = reuse.generate(
        GenerateRequest(
            mode="reuse", voice_id=original.voice_id, messages=(Message(id="third", text="three"),)
        )
    )
    # Then
    assert result.status == "complete"
    assert result.voice_id == original.voice_id
    assert boundary.seen == [("first", "second"), ("third",)]
    assert result.reference == store.voice(original.voice_id).reference
