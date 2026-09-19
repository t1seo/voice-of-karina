from pathlib import Path

from voice_of_karina.backends.interfaces import SpeechModel

def load(model_path: Path, lazy: bool = False, strict: bool = False) -> SpeechModel: ...
