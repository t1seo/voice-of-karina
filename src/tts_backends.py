#!/usr/bin/env python3
"""Pluggable voice-cloning TTS backends.

The rest of the pipeline speaks ONE common interface and ONE language convention
(ISO codes like ``ko`` / ``en``). Each backend translates to its own model API
and language dialect. Adding a new model = adding one subclass + registry entry.

    backend = get_backend("chatterbox")
    backend.load(device_info)
    audio, sr = backend.clone(text, ref_audio, ref_text, language="ko")

Backends (free / open-weight):
  * chatterbox  — Resemble AI Chatterbox Multilingual v3 (MIT)   [DEFAULT]
  * qwen3       — Qwen3-TTS 1.7B (the original backend)
  * indextts2   — IndexTTS-2 (SOTA zero-shot, strong Korean)     [optional dep]
  * cosyvoice   — CosyVoice 2/3 (Apache-2.0, cross-lingual)      [optional dep]

`clone()` returns (mono float32 numpy array, sample_rate).
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from loguru import logger

DEFAULT_BACKEND = "chatterbox"

# Full-name language map for backends that want words instead of ISO codes.
_ISO_TO_NAME = {
    "ko": "korean", "en": "english", "zh": "chinese", "ja": "japanese",
    "fr": "french", "de": "german", "it": "italian", "es": "spanish",
    "pt": "portuguese", "ru": "russian",
}


def _to_mono_f32(audio) -> np.ndarray:
    """Coerce a torch tensor / numpy array to a 1-D float32 numpy array."""
    if hasattr(audio, "detach"):  # torch tensor
        audio = audio.detach().cpu().numpy()
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.squeeze()
        if audio.ndim > 1:  # still multi-channel -> take first channel
            audio = audio[0]
    return audio


class VoiceBackend:
    """Base class. Subclasses implement `load()` and `clone()`."""

    name: str = "base"
    #: Whether this model needs the reference transcript (`ref_text`).
    needs_ref_text: bool = False

    def load(self, device_info) -> None:  # noqa: D401
        raise NotImplementedError

    def clone(self, text: str, ref_audio: Path, ref_text: str, language: str):
        raise NotImplementedError


# ------------------------------- Chatterbox ------------------------------

class ChatterboxBackend(VoiceBackend):
    name = "chatterbox"
    needs_ref_text = False  # zero-shot, no transcript needed

    def load(self, device_info) -> None:
        # Let unsupported MPS ops fall back to CPU instead of crashing.
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        device = {"cuda": "cuda", "mps": "mps"}.get(device_info.device_type.value, "cpu")
        logger.info(f"Loading Chatterbox Multilingual on {device}...")
        self.model = ChatterboxMultilingualTTS.from_pretrained(device=device)
        self.sr = int(self.model.sr)

    def clone(self, text, ref_audio, ref_text, language):
        wav = self.model.generate(
            text,
            language_id=language,               # ISO code, e.g. "ko"
            audio_prompt_path=str(ref_audio),
            exaggeration=0.5,                   # neutral, natural delivery
            cfg_weight=0.5,
        )
        return _to_mono_f32(wav), self.sr


# --------------------------------- Qwen3 ---------------------------------

class Qwen3Backend(VoiceBackend):
    name = "qwen3"
    needs_ref_text = True

    def load(self, device_info) -> None:
        from pipeline import setup_tts_model
        from qwen_tts import Qwen3TTSModel

        from device_utils import DeviceType

        self._device_info = device_info
        model_path = setup_tts_model()
        # On MPS, "sdpa" grouped-query attention breaks on torch 2.6 (mps_matmul
        # shape error); "eager" does the repeat_kv explicitly and works across
        # torch versions.
        attn = "eager" if device_info.device_type == DeviceType.MPS else device_info.attn_implementation
        logger.info(f"Loading Qwen3-TTS on {device_info.device_type.value.upper()} (attn={attn})...")
        self.model = Qwen3TTSModel.from_pretrained(
            str(model_path),
            dtype=device_info.dtype,
            attn_implementation=attn,
            device_map=device_info.torch_device,
        )

    def clone(self, text, ref_audio, ref_text, language):
        import torch

        from device_utils import DeviceType

        wavs, sr = self.model.generate_voice_clone(
            text=text,
            ref_audio=str(ref_audio),
            ref_text=ref_text,
            language=_ISO_TO_NAME.get(language, "korean"),
            non_streaming_mode=True,
        )
        if self._device_info.device_type == DeviceType.MPS:
            torch.mps.synchronize()
        return _to_mono_f32(wavs[0]), int(sr)


# ------------------------------- IndexTTS-2 ------------------------------

class IndexTTS2Backend(VoiceBackend):
    """IndexTTS-2 — SOTA zero-shot, strong Korean. Optional dependency.

    Install (Apple Silicon MLX build recommended):
        https://github.com/index-tts/index-tts
    """

    name = "indextts2"
    needs_ref_text = False

    def load(self, device_info) -> None:
        try:
            from indextts.infer_v2 import IndexTTS2  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "IndexTTS-2 not installed. See https://github.com/index-tts/index-tts "
                "(or the mlx-indextts build for Apple Silicon)."
            ) from e
        self._IndexTTS2 = IndexTTS2
        self.model = IndexTTS2()  # loads default checkpoints
        self.sr = 24000

    def clone(self, text, ref_audio, ref_text, language):
        import tempfile

        import soundfile as sf

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            out = tmp.name
        self.model.infer(spk_audio_prompt=str(ref_audio), text=text, output_path=out)
        audio, sr = sf.read(out)
        os.unlink(out)
        return _to_mono_f32(audio), int(sr)


# ------------------------------- CosyVoice -------------------------------

class CosyVoiceBackend(VoiceBackend):
    """CosyVoice 2/3 — Apache-2.0, excellent cross-lingual cloning. Optional dep.

    Install: https://github.com/FunAudioLLM/CosyVoice
    """

    name = "cosyvoice"
    needs_ref_text = True  # zero-shot inference wants the prompt transcript

    def load(self, device_info) -> None:
        try:
            from cosyvoice.cli.cosyvoice import CosyVoice2  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "CosyVoice not installed. See https://github.com/FunAudioLLM/CosyVoice"
            ) from e
        self.model = CosyVoice2("pretrained_models/CosyVoice2-0.5B")
        self.sr = int(getattr(self.model, "sample_rate", 24000))

    def clone(self, text, ref_audio, ref_text, language):
        import torch
        import torchaudio as ta

        wav, sr = ta.load(str(ref_audio))
        prompt_16k = ta.functional.resample(wav, sr, 16000)
        chunks = [
            out["tts_speech"]
            for out in self.model.inference_zero_shot(text, ref_text, prompt_16k, stream=False)
        ]
        audio = torch.cat(chunks, dim=1) if chunks else torch.zeros(1, 1)
        return _to_mono_f32(audio), self.sr


# -------------------------------- Registry -------------------------------

_BACKENDS: dict[str, type[VoiceBackend]] = {
    "chatterbox": ChatterboxBackend,
    "qwen3": Qwen3Backend,
    "indextts2": IndexTTS2Backend,
    "cosyvoice": CosyVoiceBackend,
}


def available_backends() -> list[str]:
    return list(_BACKENDS)


def get_backend(name: str) -> VoiceBackend:
    key = (name or DEFAULT_BACKEND).lower()
    if key not in _BACKENDS:
        raise ValueError(f"Unknown backend {name!r}. Choose from: {', '.join(_BACKENDS)}")
    return _BACKENDS[key]()
