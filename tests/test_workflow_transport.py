import os
import subprocess
import sys
from pathlib import Path

from voice_of_karina.engine import ErrorResult, run
from voice_of_karina.workflow_models import JobResult


def test_invalid_url_when_transport_parses_does_not_create_state(tmp_path: Path) -> None:
    # Given
    root = tmp_path / "state"
    # When
    result = run('{"action":"analyze","sources":["https://example.com/audio"]}', root)
    # Then
    assert isinstance(result, ErrorResult)
    assert result.errors[0].code == "invalid_source"
    assert not root.exists()


def test_malformed_json_when_transport_parses_is_structured(tmp_path: Path) -> None:
    # Given / When
    result = run('{"action":', tmp_path / "state")
    # Then
    assert isinstance(result, ErrorResult)
    assert result.errors[0].code == "invalid_request"
    assert not (tmp_path / "state").exists()


def test_request_file_when_engine_invoked_emits_one_resumable_json(tmp_path: Path) -> None:
    # Given
    request = tmp_path / "request.json"
    _ = request.write_text('{"action":"analyze","sources":[]}', encoding="utf-8")
    environment = {**os.environ, "VOICE_OF_KARINA_HOME": str(tmp_path / "state")}
    # When
    result = subprocess.run(  # noqa: S603 - fixed module and fixture-owned request path
        [sys.executable, "-m", "voice_of_karina", "--request", str(request)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    # Then
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.splitlines()) == 1
    parsed = JobResult.model_validate_json(result.stdout)
    assert parsed.status == "needs_input"
    assert (tmp_path / "state" / "jobs" / parsed.job_id / "job.json").is_file()


def test_unknown_status_when_stdin_invoked_returns_failure_json(tmp_path: Path) -> None:
    # Given
    environment = {**os.environ, "VOICE_OF_KARINA_HOME": str(tmp_path / "state")}
    # When
    result = subprocess.run(
        [sys.executable, "-m", "voice_of_karina"],
        input='{"action":"status","job_id":"missing"}',
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    # Then
    assert result.returncode == 1
    assert ErrorResult.model_validate_json(result.stdout).errors[0].code == "unknown_job"
    assert not (tmp_path / "state" / "jobs").exists()
