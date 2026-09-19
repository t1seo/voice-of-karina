from pathlib import Path

from voice_of_karina.backends.interfaces import QwenModel

def load_model(model_path: Path, lazy: bool = False, strict: bool = True) -> QwenModel: ...
