from pathlib import Path

import pytest

from voice_of_karina.contracts import (
    AnalysisResult,
    AnalyzeRequest,
    GenerateRequest,
    Message,
    Provenance,
    Reference,
)
from voice_of_karina.errors import VoiceError
from voice_of_karina.storage import Store, file_digest
from voice_of_karina.workflow_models import Adapters, JobResult, MessageResult
from voice_of_karina.workflow_reference import analyze_for_generation, validate_reference


@pytest.mark.parametrize(
    ("contents", "transcript"),
    [(None, "hello"), (b"changed", "hello"), (b"original", " \n ")],
    ids=["missing-audio", "changed-audio", "blank-transcript"],
)
def test_reference_when_incomplete_or_changed_is_rejected(
    tmp_path: Path, contents: bytes | None, transcript: str
) -> None:
    # Given
    path = tmp_path / "reference.wav"
    _ = path.write_bytes(b"original")
    reference = Reference(
        audio_path=str(path),
        sha256=file_digest(path),
        transcript=transcript,
        provenance=Provenance(kind="mimic", source="source.wav"),
    )
    if contents is None:
        path.unlink()
    else:
        _ = path.write_bytes(contents)
    # When
    with pytest.raises(VoiceError) as raised:
        validate_reference(reference)
    # Then
    assert raised.value.code == "invalid_reference"


@pytest.mark.parametrize(
    ("analysis", "sources", "code"),
    [(None, (), "missing_analysis"), (AnalysisResult(), ("other.wav",), "conflicting_sources")],
    ids=["missing-analysis", "conflicting-source"],
)
def test_analysis_job_when_missing_or_from_other_sources_is_rejected(
    tmp_path: Path, analysis: AnalysisResult | None, sources: tuple[str, ...], code: str
) -> None:
    # Given
    store = Store(tmp_path)
    previous = store.save(
        JobResult(
            job_id="job-analysis",
            request=AnalyzeRequest(sources=("source.wav",)),
            analysis=analysis,
        )
    )
    request = GenerateRequest(
        mode="mimic",
        sources=sources,
        analysis_job_id=previous.job_id,
        messages=(Message(id="done", text="hello"),),
    )
    state = JobResult(
        job_id="job-generation",
        request=request,
        messages=(MessageResult(message_id="done", text="hello"),),
    )
    adapters = Adapters(
        lambda _request, _directory: pytest.fail("Rejected analysis cannot run ASR"),
        lambda _candidate, _directory: pytest.fail("Rejected analysis cannot prepare"),
        lambda _request, _directory: pytest.fail("Rejected analysis cannot design"),
        lambda _request, _reference, _directory: pytest.fail("Rejected analysis cannot synthesize"),
        lambda _audio, _text, _policy: pytest.fail("Rejected analysis cannot validate outputs"),
    )
    # When
    with pytest.raises(VoiceError) as raised:
        _ = analyze_for_generation(state, request, store, adapters)
    # Then
    assert raised.value.code == code
