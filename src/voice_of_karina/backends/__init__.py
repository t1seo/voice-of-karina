"""Local-first voice backend; importing this API never loads model weights."""

from .models import backend_cache_key
from .tts import design_reference, synthesize

__all__ = ["backend_cache_key", "design_reference", "synthesize"]
