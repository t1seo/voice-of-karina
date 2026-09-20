import json
from pathlib import Path

import pytest

from tests.audition_helpers import AuditionBackend, audition_request
from voice_of_karina import engine
from voice_of_karina.audition_models import AuditionResult
from voice_of_karina.curation_contracts import ProfileResult
from voice_of_karina.storage import Store
from voice_of_karina.workflow_models import JobResult


def test_skill_transport_compare_select_then_reuse_restores_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = audition_request(Store(tmp_path / "store"), tmp_path)
    backend = AuditionBackend()
    monkeypatch.setattr(engine, "synthesize", backend.synthesize)
    monkeypatch.setattr(engine, "validate_output", backend.validate)
    result = engine.run(request.model_dump_json(), tmp_path / "store")
    assert isinstance(result, AuditionResult)
    assert result.status == "needs_selection"
    assert len(backend.calls) == 4
    for action in ("status", "resume"):
        repeated = engine.run(
            json.dumps({"action": action, "job_id": result.job_id}), tmp_path / "store"
        )
        assert isinstance(repeated, AuditionResult)
        assert repeated.choices == result.choices
    assert len(backend.calls) == 4
    selected = engine.run(
        json.dumps(
            {
                "action": "select_voice",
                "job_id": result.job_id,
                "choice_id": result.choices[0].choice_id,
                "name": "선택한 음성",
                "reason": "Transport test selection; no listening approval.",
            }
        ),
        tmp_path / "store",
    )
    assert isinstance(selected, ProfileResult)
    generated = engine.run(
        json.dumps(
            {
                "action": "generate",
                "mode": "reuse",
                "voice_id": selected.voice.id,
                "messages": [{"id": "new", "text": "새 문장도 준비됐어요."}],
            }
        ),
        tmp_path / "store",
    )
    assert isinstance(generated, JobResult)
    assert generated.status == "complete"
    assert generated.recipe == selected.voice.recipe
    assert len(backend.calls) == 5
    assert selected.voice.recipe is not None
    assert backend.calls[-1].settings == selected.voice.recipe.settings


@pytest.mark.parametrize("action", ["status", "resume"])
def test_unknown_audition_transport_does_not_create_job(tmp_path: Path, action: str) -> None:
    response = engine.run(json.dumps({"action": action, "job_id": "audition-missing"}), tmp_path)
    assert isinstance(response, engine.ErrorResult)
    assert response.errors[0].code == "unknown_audition"
    assert not (tmp_path / "jobs").exists()


@pytest.mark.parametrize("seed", [True, "5", 1.1, -1, 2**32])
def test_audition_transport_refuses_non_uint32_seed(
    tmp_path: Path, seed: bool | str | float
) -> None:
    response = engine.run(
        json.dumps(
            {
                "action": "audition",
                "voice_ids": ["voice-a", "voice-b"],
                "messages": [{"id": "m", "text": "안녕하세요"}],
                "seeds": [seed],
            }
        ),
        tmp_path,
    )
    assert isinstance(response, engine.ErrorResult)
    assert response.errors[0].code == "invalid_request"
    assert not (tmp_path / "jobs").exists()
