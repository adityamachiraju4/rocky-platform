"""Provider boundary for speech rendering."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class SpeechProviderError(RuntimeError):
    """Raised when a speech provider cannot safely render audio."""

    def __init__(self, message: str, *, code: str = "provider_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SpeechAudio:
    content: bytes
    media_type: str


class SpeechProvider(Protocol):
    async def synthesize(self, text: str) -> SpeechAudio: ...


class FallbackSpeechProvider:
    def __init__(self, providers: tuple[tuple[str, SpeechProvider], ...]) -> None:
        self._providers = providers

    async def synthesize(self, text: str) -> SpeechAudio:
        last_error: SpeechProviderError | None = None
        for name, provider in self._providers:
            try:
                audio = await provider.synthesize(text)
            except SpeechProviderError as exc:
                last_error = exc
                logger.warning(
                    "Speech provider failed",
                    extra={
                        "speech_provider": name,
                        "provider_error_code": exc.code,
                    },
                )
                continue
            logger.info(
                "Speech provider succeeded",
                extra={"speech_provider": name},
            )
            return audio

        raise SpeechProviderError(
            "No speech provider could render audio.",
            code=last_error.code if last_error is not None else "unavailable",
        )
