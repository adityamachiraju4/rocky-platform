"""TranscriptionService turns uploaded microphone audio into text."""
from __future__ import annotations

from app.transcription.provider import (
    TranscriptionProvider,
    TranscriptionProviderError,
)

MAX_TRANSCRIPTION_AUDIO_BYTES = 12 * 1024 * 1024
SUPPORTED_TRANSCRIPTION_MEDIA_TYPES: frozenset[str] = frozenset(
    {
        "audio/webm",
        "audio/ogg",
        "audio/mp4",
        "audio/mpeg",
        "audio/mp3",
        "audio/wav",
        "audio/wave",
        "audio/x-wav",
        "audio/aac",
        "audio/m4a",
    }
)


class TranscriptionInputError(ValueError):
    """Raised when uploaded audio is invalid."""


class TranscriptionUnavailableError(RuntimeError):
    """Raised when transcription is unavailable for this deployment."""


class TranscriptionService:
    def __init__(self, provider: TranscriptionProvider | None) -> None:
        self._provider = provider

    async def transcribe(
        self, audio: bytes, *, filename: str, content_type: str | None
    ) -> str:
        media_type = (content_type or "").split(";")[0].strip().lower()
        if not audio:
            raise TranscriptionInputError("Audio upload is empty.")
        if len(audio) > MAX_TRANSCRIPTION_AUDIO_BYTES:
            raise TranscriptionInputError("Audio upload is too large.")
        if media_type not in SUPPORTED_TRANSCRIPTION_MEDIA_TYPES:
            raise TranscriptionInputError("Audio format is not supported.")
        if self._provider is None:
            raise TranscriptionUnavailableError(
                "Transcription provider is unavailable."
            )
        try:
            text = await self._provider.transcribe(
                audio,
                filename=filename or "audio",
                content_type=media_type,
            )
        except TranscriptionProviderError as exc:
            raise TranscriptionUnavailableError(
                "Transcription provider failed."
            ) from exc
        if not text.strip():
            raise TranscriptionUnavailableError("Transcription was empty.")
        return text.strip()
