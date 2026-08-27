"""OpenAI-backed speech-to-text provider."""
from __future__ import annotations

import logging
from typing import Any

from app.transcription.provider import TranscriptionProviderError, TranscriptionResult

logger = logging.getLogger(__name__)

_MAX_LOG_MESSAGE_LENGTH = 240


def _safe_error_message(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    compact = " ".join(value.split())
    if not compact:
        return None
    return compact[:_MAX_LOG_MESSAGE_LENGTH]


def _openai_error_metadata(exc: Exception) -> dict[str, object]:
    status_code = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error: Any = body["error"]
    else:
        error = body

    error_type = getattr(exc, "type", None)
    error_code = getattr(exc, "code", None)
    message: object = getattr(exc, "message", None) or str(exc)

    if isinstance(error, dict):
        error_type = error.get("type") or error_type
        error_code = error.get("code") or error_code
        message = error.get("message") or message

    return {
        "status": status_code,
        "error_type": error_type or exc.__class__.__name__,
        "error_code": error_code,
        "upstream_message": _safe_error_message(message),
    }


class OpenAITranscriptionProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - environment guard
            raise TranscriptionProviderError(
                "OpenAI SDK is not installed."
            ) from exc

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )
        self._base_url = base_url
        self._model = model
        self.last_error_code: str | None = None

    async def transcribe(
        self, audio: bytes, *, filename: str, content_type: str
    ) -> TranscriptionResult:
        self.last_error_code = None
        try:
            response = await self._client.audio.transcriptions.create(
                model=self._model,
                file=(filename, audio, content_type),
            )
        except Exception as exc:  # noqa: BLE001 - endpoint returns 503
            self.last_error_code = "request_failed"
            metadata = _openai_error_metadata(exc)
            logger.warning(
                "OpenAI transcription request failed: "
                "status=%s error_type=%s error_code=%s "
                "upstream_message=%s model=%s base_url=%s "
                "audio_filename=%s content_type=%s",
                metadata["status"],
                metadata["error_type"],
                metadata["error_code"],
                metadata["upstream_message"],
                self._model,
                self._base_url,
                filename,
                content_type,
                extra={
                    "provider_error_code": self.last_error_code,
                    "status": metadata["status"],
                    "error_type": metadata["error_type"],
                    "error_code": metadata["error_code"],
                    "upstream_message": metadata["upstream_message"],
                    "model": self._model,
                    "base_url": self._base_url,
                    "audio_filename": filename,
                    "content_type": content_type,
                },
            )
            raise TranscriptionProviderError(
                "OpenAI transcription request failed: "
                f"{metadata['upstream_message']}",
                code=self.last_error_code,
            ) from exc

        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            self.last_error_code = "empty_transcription"
            logger.warning(
                "OpenAI transcription response returned empty text",
                extra={"provider_error_code": self.last_error_code},
            )
            raise TranscriptionProviderError(
                "OpenAI transcription response returned empty text.",
                code=self.last_error_code,
            )
        language = getattr(response, "language", None)
        return TranscriptionResult(
            text=text.strip(),
            language=language if isinstance(language, str) else None,
        )
