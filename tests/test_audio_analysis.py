from __future__ import annotations

import wave
from typing import TYPE_CHECKING

from voice_of_karina import audio
from voice_of_karina.audio_metrics import SpeechFrame, utterance_windows
from voice_of_karina.contracts import AnalyzeRequest

if TYPE_CHECKING:
    from pathlib import Path


def test_silent_source_requests_another_recording_without_running_stt(tmp_path: Path) -> None:
    # Given a real WAV containing no speech or background signal.
    source = tmp_path / "silent.wav"
    with wave.open(str(source), "wb") as output:
        output.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        output.writeframes(b"\x00\x00" * 48000 * 4)
    request = AnalyzeRequest(sources=(str(source),))
    # When the conversational analysis stage runs.
    result = audio.analyze_sources(request, tmp_path / "job")
    # Then it offers no invented candidate or transcript.
    assert result.candidates == ()
    assert result.input_request is not None
    assert "speech" in result.input_request.lower()


def test_utterance_windows_end_at_a_speech_pause() -> None:
    # Given two utterances separated by a natural pause.
    frames = (SpeechFrame(speech=True, rms=0.1),) * 150 + (SpeechFrame(speech=False, rms=0),) * 30
    frames += (SpeechFrame(speech=True, rms=0.1),) * 150
    # When candidate intervals are derived.
    intervals = utterance_windows(frames)
    # Then the pause separates complete utterances instead of a loudest window.
    assert len(intervals) == 2
    assert intervals[0][1] < intervals[1][0]


def test_short_speech_is_not_presented_as_a_clone_reference() -> None:
    # Given speech lasting less than three seconds.
    frames = (SpeechFrame(speech=True, rms=0.1),) * 50
    # When candidate intervals are derived.
    intervals = utterance_windows(frames)
    # Then the insufficient reference is excluded.
    assert intervals == ()
