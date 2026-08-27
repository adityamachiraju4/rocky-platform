"""SpeechService renders already-grounded Rocky text into audio."""
from __future__ import annotations

import re

from app.speech.provider import SpeechAudio, SpeechProvider, SpeechProviderError


class SpeechUnavailableError(RuntimeError):
    """Raised when neural speech is unavailable for this deployment."""


class SpeechService:
    def __init__(self, provider: SpeechProvider | None) -> None:
        self._provider = provider

    async def synthesize(self, text: str, *, language: str = "en") -> SpeechAudio:
        if self._provider is None:
            raise SpeechUnavailableError("Speech provider is unavailable.")
        try:
            return await self._provider.synthesize(
                _speech_text(text), language=language
            )
        except SpeechProviderError as exc:
            raise SpeechUnavailableError("Speech provider failed.") from exc


def _speech_text(text: str) -> str:
    rendered = text.strip()
    rendered = rendered.replace(" -- ", " — ")
    rendered = re.sub(r"\s+", " ", rendered)
    return rendered
