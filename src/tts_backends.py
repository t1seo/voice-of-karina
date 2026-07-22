#!/usr/bin/env python3
"""Voice-cloning TTS backend(s).

The pipeline speaks ONE common interface and ONE language convention (ISO codes
like ``ko`` / ``en``); the backend translates to its model's API and dialect.

    backend = get_backend("qwen3")
    backend.load(device_info)
    audio, sr = backend.clone(text, ref_audio, ref_text, language="ko")

Currently the only backend is Qwen3-TTS 1.7B. `clone()` returns
(mono float32 numpy array, sample_rate).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from loguru import logger

DEFAULT_BACKEND = "qwen3"

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


class Qwen3Backend(VoiceBackend):
    name = "qwen3"
    needs_ref_text = True

    def load(self, device_info) -> None:
        from device_utils import DeviceType
        from pipeline import setup_tts_model
        from qwen_tts import Qwen3TTSModel

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


_BACKENDS: dict[str, type[VoiceBackend]] = {
    "qwen3": Qwen3Backend,
}


def available_backends() -> list[str]:
    return list(_BACKENDS)


def get_backend(name: str) -> VoiceBackend:
    key = (name or DEFAULT_BACKEND).lower()
    if key not in _BACKENDS:
        raise ValueError(f"Unknown backend {name!r}. Choose from: {', '.join(_BACKENDS)}")
    return _BACKENDS[key]()
