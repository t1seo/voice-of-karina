from pathlib import Path

import pytest

from voice_of_karina.contracts import AnalyzeRequest
from voice_of_karina.errors import VoiceError
from voice_of_karina.storage import Store
from voice_of_karina.workflow_models import JobResult


def test_unknown_job_when_loading_does_not_create_state(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path)
    # When / Then
    with pytest.raises(VoiceError, match="No saved job"):
        _ = store.load("missing")
    assert not (tmp_path / "jobs" / "missing" / "job.json").exists()


def test_path_traversal_when_loading_is_rejected(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path)
    # When / Then
    with pytest.raises(VoiceError, match="identifier"):
        _ = store.load("../../escape")


def test_duplicate_lock_when_job_running_is_rejected(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path)
    # When / Then
    with (
        store.lock("job-a"),
        pytest.raises(VoiceError, match="already running"),
        store.lock("job-a"),
    ):
        pytest.fail("The second process lock must not be acquired")


def test_atomic_state_when_saved_roundtrips(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path)
    state = JobResult(job_id="job-a", request=AnalyzeRequest(), status="needs_input")
    # When
    _ = store.save(state)
    # Then
    assert store.load("job-a") == state
    assert tuple((tmp_path / "jobs" / "job-a").iterdir()) == (
        tmp_path / "jobs" / "job-a" / "job.json",
    )


def test_job_identity_when_local_source_bytes_change_is_invalidated(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path / "store")
    source = tmp_path / "source.wav"
    _ = source.write_bytes(b"first-recording")
    request = AnalyzeRequest(sources=(str(source),))
    first = store.request_id(request)
    # When
    _ = source.write_bytes(b"different-recording")
    # Then
    assert store.request_id(request) != first


def test_job_identity_when_model_revision_changes_is_invalidated(tmp_path: Path) -> None:
    # Given
    request = AnalyzeRequest()
    original = Store(tmp_path, cache_key="model-revision-a")
    updated = Store(tmp_path, cache_key="model-revision-b")
    # When / Then
    assert original.request_id(request) != updated.request_id(request)


def test_job_identity_when_home_shorthand_source_changes_is_invalidated(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path / "store")
    source = tmp_path / "source.wav"
    _ = source.write_bytes(b"first-recording")
    shorthand = "~/" + str(source.relative_to(Path.home(), walk_up=True))
    request = AnalyzeRequest(sources=(shorthand,))
    first = store.request_id(request)
    # When
    _ = source.write_bytes(b"different-recording")
    # Then
    assert store.request_id(request) != first


def test_job_identity_when_source_aliases_share_canonical_path_is_equal(tmp_path: Path) -> None:
    # Given
    store = Store(tmp_path / "store")
    source = tmp_path / "source.wav"
    _ = source.write_bytes(b"same-recording")
    shorthand = "~/" + str(source.relative_to(Path.home(), walk_up=True))
    absolute = AnalyzeRequest(sources=(str(source.resolve()),))
    # When
    aliased = store.request_id(AnalyzeRequest(sources=(shorthand,)))
    # Then
    assert aliased == store.request_id(absolute)
