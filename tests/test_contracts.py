import pytest
from pydantic import ValidationError

from voice_of_karina.contracts import GenerateRequest
from voice_of_karina.requests import REQUEST_ADAPTER


@pytest.mark.parametrize(
    "payload",
    [
        '{"action":"generate","mode":"design","messages":[]}',
        '{"action":"generate","mode":"design","messages":[{"id":"ok","text":" "}]}',
        '{"action":"generate","mode":"design","messages":[{"id":"../escape","text":"hello"}]}',
        '{"action":"generate","mode":"design","messages":[{"id":"ok","text":"hello"}],"max_attempts":4}',
        '{"action":"analyze","sources":["clip.wav"],"source_time":{"source_index":1,"start_seconds":1,"end_seconds":2}}',
        '{"action":"analyze","sources":["clip.wav"],"source_time":{"start_seconds":2,"end_seconds":1}}',
        '{"action":"generate","mode":"design","sources":["clip.wav"],"messages":[{"id":"ok","text":"hello"}]}',
        '{"action":"resume","job_id":"job-a","max_attempts":3}',
    ],
)
def test_request_when_invalid_is_rejected(payload: str) -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        _ = REQUEST_ADAPTER.validate_json(payload)


def test_generation_when_valid_preserves_exact_requested_lines() -> None:
    # Given
    payload = (
        '{"action":"generate","mode":"design","voice_description":"calm",'
        '"messages":[{"id":"done","text":"완료했습니다."}]}'
    )
    # When
    request = REQUEST_ADAPTER.validate_json(payload)
    # Then
    assert isinstance(request, GenerateRequest)
    assert [(m.id, m.text) for m in request.messages] == [("done", "완료했습니다.")]
    assert request.max_attempts == 2
