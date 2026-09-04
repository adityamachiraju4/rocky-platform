"""OpenAI-backed speech provider for Rocky's spoken replies."""
from __future__ import annotations

import logging

from app.speech.diagnostics import openai_exception_details
from app.speech.provider import SpeechAudio, SpeechProviderError

logger = logging.getLogger(__name__)

ROCKY_VOICE_INSTRUCTIONS = (
    "Speak naturally and conversationally. Calm, warm, confident, concise. "
    "Use subtle emphasis. Avoid announcer-style delivery. Do not sound overly "
    "cheerful or theatrical."
)


class OpenAISpeechProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        voice: str,
        speed: float,
        timeout_seconds: float,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - environment guard
            raise SpeechProviderError("OpenAI SDK is not installed.") from exc

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )
        self._model = model
        self._voice = voice
        self._speed = speed
        self.last_error_code: str | None = None

    async def synthesize(
        self, text: str, *, language: str = "en"
    ) -> SpeechAudio:
        self.last_error_code = None
        try:
            response = await self._client.audio.speech.create(
                model=self._model,
                voice=self._voice,
                input=text,
                instructions=(
                    f"{ROCKY_VOICE_INSTRUCTIONS} Speak in language code "
                    f"{language}."
                ),
                response_format="mp3",
                speed=self._speed,
            )
            content = response.content
        except Exception as exc:  # noqa: BLE001 - endpoint returns 503
            self.last_error_code = "request_failed"
            details = openai_exception_details(exc)
            logger.warning(
                "OpenAI speech request failed: status=%s error_type=%s "
                "error_code=%s reason=%s model=%s",
                details["status"],
                details["error_type"],
                details["error_code"],
                details["reason"],
                self._model,
                extra={
                    "speech_provider": "openai",
                    "provider_error_code": self.last_error_code,
                    **details,
                    "model": self._model,
                },
            )
            raise SpeechProviderError(
                "OpenAI speech request failed.",
                code=self.last_error_code,
                error_type=str(details["error_type"]),
                reason=str(details["reason"]),
            ) from exc

        if not content:
            self.last_error_code = "empty_audio"
            logger.warning(
                "OpenAI speech response returned empty audio",
                extra={"provider_error_code": self.last_error_code},
            )
            raise SpeechProviderError(
                "OpenAI speech response returned empty audio.",
                code=self.last_error_code,
                error_type="EmptyAudio",
                reason="OpenAI returned an empty audio response.",
            )

        return SpeechAudio(content=content, media_type="audio/mpeg")
