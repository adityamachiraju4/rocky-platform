"""Provider boundary for speech-to-text transcription."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str | None = None
    language_probability: float | None = None


class TranscriptionProviderError(RuntimeError):
    """Raised when a provider cannot safely transcribe audio."""

    def __init__(self, message: str, *, code: str = "provider_error") -> None:
        super().__init__(message)
        self.code = code


class TranscriptionProvider(Protocol):
    async def transcribe(
        self, audio: bytes, *, filename: str, content_type: str
    ) -> TranscriptionResult: ...
