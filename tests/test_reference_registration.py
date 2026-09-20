from __future__ import annotations

import wave
from pathlib import Path

import pytest

from voice_of_karina import preset_catalog, reference_registration
from voice_of_karina.backends.models import CLONE_MODEL
from voice_of_karina.backends.stt import Transcript, TranscriptSegment
from voice_of_karina.contracts import Provenance, Reference
from voice_of_karina.curation_contracts import PresetRequest, RegisterRequest
from voice_of_karina.errors import VoiceError
from voice_of_karina.generation_settings import GenerationSettings, VoiceRecipe
from voice_of_karina.storage import Store, file_digest


def wav_reference(
    path: Path, *, channels: int = 1, rate: int = 48000, duration: float = 3.5, silent: bool = False
) -> Reference:
    with wave.open(str(path), "wb") as output:
        output.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        samples = b"\x00\x00" if silent else b"\x88\x13"
        output.writeframes(samples * round(rate * duration) * channels)
    return Reference(
        audio_path=str(path),
        sha256=file_digest(path),
        transcript="오늘 재밌는 시간 함께 보내봅시다",
        provenance=Provenance(kind="mimic", source="https://example.test/interview"),
    )


def transcript(text: str, *, uncertain: bool = False) -> Transcript:
    return Transcript(
        text=text,
        segments=(
            TranscriptSegment(
                start=0.1,
                end=3.1,
                text=text,
                avg_logprob=-2 if uncertain else -0.1,
                no_speech_prob=0.01,
            ),
        ),
    )


def test_registration_copies_exact_bytes_and_preserves_recipe_after_source_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a supplied exact native reference and independent ASR of its frozen copy.
    reference = wav_reference(tmp_path / "source.wav")
    seen: list[Path] = []

    def recognize(path: Path, *, language: str | None = "ko") -> Transcript:
        seen.append(path)
        assert path != Path(reference.audio_path)
        assert language == "ko"
        return transcript(reference.transcript)

    monkeypatch.setattr(reference_registration, "transcribe_audio", recognize)
    recipe = VoiceRecipe(
        settings=GenerationSettings(seed=17),
        model_id=CLONE_MODEL.repository,
        model_revision=CLONE_MODEL.revision,
    )
    store = Store(tmp_path / "state")
    # When registration validates and copies it into durable profile storage.
    result = reference_registration.register_voice(
        RegisterRequest(name="Saved voice", reference=reference, recipe=recipe), store
    )
    Path(reference.audio_path).unlink()
    # Then reuse preserves the exact samples and settings independently of the source.
    saved = store.voice(result.voice.id)
    assert result.status == "complete"
    assert saved.reference.sha256 == reference.sha256
    assert file_digest(Path(saved.reference.audio_path)) == reference.sha256
    assert saved.recipe == recipe
    assert len(seen) == 1


@pytest.mark.parametrize(
    "problem", ["digest", "stereo", "short", "silent", "container", "truncated"]
)
def test_registration_rejects_invalid_reference_before_running_asr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    # Given reference bytes that fail the exact digest or physical recording requirements.
    reference = wav_reference(
        tmp_path / "source.wav",
        channels=2 if problem == "stereo" else 1,
        duration=2.5 if problem == "short" else 3.5,
        silent=problem == "silent",
    )
    path = Path(reference.audio_path)
    if problem == "digest":
        reference = reference.model_copy(update={"sha256": "0" * 64})
    if problem == "container":
        _ = path.write_bytes(b"not a WAV recording")
        reference = reference.model_copy(update={"sha256": file_digest(path)})
    if problem == "truncated":
        _ = path.write_bytes(path.read_bytes()[:100])
        reference = reference.model_copy(update={"sha256": file_digest(path)})

    def forbidden(path: Path, *, language: str | None = "ko") -> Transcript:
        pytest.fail(f"Invalid source must not reach ASR: {path}, {language}")

    monkeypatch.setattr(reference_registration, "transcribe_audio", forbidden)
    store = Store(tmp_path / "state")
    # When exact reference registration is requested.
    with pytest.raises(VoiceError):
        _ = reference_registration.register_voice(
            RegisterRequest(name="Invalid", reference=reference), store
        )
    # Then no reusable profile is created.
    assert store.voices() == ()


@pytest.mark.parametrize("problem", ["mismatch", "ending", "uncertain", "unavailable", "mutation"])
def test_registration_requires_reliable_independent_text_and_unchanged_frozen_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    # Given unreliable text, an unavailable recognizer, or bytes changed during recognition.
    reference = wav_reference(tmp_path / "source.wav")

    def recognize(path: Path, *, language: str | None = "ko") -> Transcript:
        assert language == "ko"
        if problem == "unavailable":
            raise VoiceError("asr_unavailable", "No transcription runtime")
        if problem == "mutation":
            _ = path.write_bytes(b"changed")
        text = reference.transcript
        if problem == "mismatch":
            text = "다른 이야기입니다"
        if problem == "ending":
            text = reference.transcript[:-1]
        return transcript(text, uncertain=problem == "uncertain")

    monkeypatch.setattr(reference_registration, "transcribe_audio", recognize)
    store = Store(tmp_path / "state")
    # When the supplied text is checked against the frozen audio.
    with pytest.raises(VoiceError):
        _ = reference_registration.register_voice(
            RegisterRequest(name="Unverified", reference=reference), store
        )
    # Then registration cannot silently approve the supplied transcript.
    assert store.voices() == ()


def test_registration_rejects_staging_path_escape(tmp_path: Path) -> None:
    # Given a store whose staging parent was replaced with an external directory link.
    reference = wav_reference(tmp_path / "source.wav")
    store = Store(tmp_path / "state")
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "jobs").symlink_to(outside, target_is_directory=True)
    # When reference registration attempts to create its frozen copy.
    with pytest.raises(VoiceError) as raised:
        _ = reference_registration.register_voice(
            RegisterRequest(name="Escaped", reference=reference), store
        )
    # Then no external file is written.
    assert raised.value.code == "invalid_path"
    assert not tuple(outside.iterdir())


def test_packaged_reviewed_preset_registers_the_exact_known_reference_and_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given the versioned preset included with the engine rather than local experiment files.
    def recognize(path: Path, *, language: str | None = "ko") -> Transcript:
        assert path.is_file()
        assert language == "ko"
        return transcript("오늘 재밌는 시간 함께 보내봅시다")

    monkeypatch.setattr(reference_registration, "transcribe_audio", recognize)
    # When the conversational preset operation registers it into a fresh store.
    result = preset_catalog.register_preset(
        PresetRequest(preset_id="karina-reviewed-v1"), Store(tmp_path / "state")
    )
    # Then exact original samples, provenance, and supported sampling controls survive.
    assert result.voice.name == "카리나 검토 참조"
    assert result.voice.reference.sha256 == (
        "22133aea2c959269cd804edfdd9bcb1b29945d80b9002782aecfcb15f25d898f"
    )
    assert result.voice.reference.provenance.start_seconds == 24.419104166666667
    assert result.voice.recipe is not None
    assert result.voice.recipe.settings == GenerationSettings(seed=20260920)
    assert result.voice.recipe.model_revision == CLONE_MODEL.revision


def test_packaged_preset_rejects_an_unknown_identifier(tmp_path: Path) -> None:
    # Given an otherwise valid request for a preset absent from the installed package.
    # When the catalog is consulted.
    with pytest.raises(VoiceError) as raised:
        _ = preset_catalog.register_preset(
            PresetRequest(preset_id="unknown-voice-v1"), Store(tmp_path / "state")
        )
    # Then the caller receives an actionable catalog failure.
    assert raised.value.code == "unknown_preset"


@pytest.mark.parametrize("escape", ["manifest", "audio_symlink", "relative", "absolute"])
def test_packaged_preset_rejects_paths_outside_its_resource_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, escape: str
) -> None:
    # Given a catalog whose resource path or manifest points outside its package directory.
    original = preset_catalog.PRESET_ROOT / "karina-reviewed-v1" / "manifest.json"
    manifest = preset_catalog.PresetManifest.model_validate_json(original.read_bytes())
    catalog = tmp_path / "catalog"
    directory = catalog / manifest.id
    directory.mkdir(parents=True)
    outside = tmp_path / "outside.wav"
    _ = outside.write_bytes(b"external resource")
    if escape in {"relative", "absolute"}:
        reference_path = "../../outside.wav" if escape == "relative" else str(outside)
        manifest = manifest.model_copy(
            update={
                "reference": manifest.reference.model_copy(update={"audio_path": reference_path})
            }
        )
    if escape == "audio_symlink":
        (directory / "reference.wav").symlink_to(outside)
    if escape == "manifest":
        outside_manifest = tmp_path / "manifest.json"
        _ = outside_manifest.write_text(manifest.model_dump_json(), encoding="utf-8")
        (directory / "manifest.json").symlink_to(outside_manifest)
    else:
        _ = (directory / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(preset_catalog, "PRESET_ROOT", catalog)

    def forbidden(path: Path, *, language: str | None = "ko") -> Transcript:
        pytest.fail(f"Escaped preset must not reach ASR: {path}, {language}")

    monkeypatch.setattr(reference_registration, "transcribe_audio", forbidden)
    # When the private preset action resolves its packaged reference.
    with pytest.raises(VoiceError) as raised:
        _ = preset_catalog.register_preset(
            PresetRequest(preset_id=manifest.id), Store(tmp_path / "state")
        )
    # Then reference registration cannot follow the escaped path.
    assert raised.value.code == "invalid_preset"
