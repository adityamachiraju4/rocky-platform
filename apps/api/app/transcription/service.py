"""TranscriptionService turns uploaded microphone audio into text."""
from __future__ import annotations

import re

from app.transcription.provider import (
    TranscriptionProvider,
    TranscriptionProviderError,
    TranscriptionResult,
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


class TranscriptionNoSpeechError(RuntimeError):
    """Raised when audio was processed but no speech was recognized."""


_NON_SPEECH_MARKERS = frozenset(
    {
        "[music]",
        "(music)",
        "[silence]",
        "(silence)",
        "<|nospeech|>",
    }
)


def _is_unusable_transcript(text: str) -> bool:
    """Reject only structurally empty output, preserving short commands."""

    normalized = " ".join(text.split()).lower()
    if normalized in _NON_SPEECH_MARKERS:
        return True
    word_characters = re.findall(r"\w", normalized, flags=re.UNICODE)
    return len(word_characters) < 2


class TranscriptionService:
    def __init__(self, provider: TranscriptionProvider | None) -> None:
        self._provider = provider

    async def transcribe(
        self, audio: bytes, *, filename: str, content_type: str | None
    ) -> TranscriptionResult:
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
            result = await self._provider.transcribe(
                audio,
                filename=filename or "audio",
                content_type=media_type,
            )
        except TranscriptionProviderError as exc:
            if exc.code == "empty_transcription":
                raise TranscriptionNoSpeechError(
                    "No speech was recognized in the audio."
                ) from exc
            raise TranscriptionUnavailableError(
                "Transcription provider failed."
            ) from exc
        if isinstance(result, str):
            result = TranscriptionResult(text=result)
        if _is_unusable_transcript(result.text):
            raise TranscriptionNoSpeechError(
                "No speech was recognized in the audio."
            )
        return TranscriptionResult(
            text=result.text.strip(),
            language=result.language,
            language_probability=result.language_probability,
            raw_language=result.raw_language,
            raw_language_probability=result.raw_language_probability,
        )
