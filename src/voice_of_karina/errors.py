"""Typed failures that can safely be reported by the agent transport."""


class VoiceError(Exception):
    """An actionable domain error with a stable machine-readable code."""

    code: str
    message: str

    def __init__(self, code: str, message: str) -> None:
        """Keep a stable code alongside the user-facing explanation."""
        self.code = code
        self.message = message
        super().__init__(message)
