"""Dependency-injection wiring for the Speech rendering capability."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends

from app.core import settings
from app.speech.kokoro_provider import KokoroSpeechProvider
from app.speech.openai_provider import OpenAISpeechProvider
from app.speech.provider import (
    FallbackSpeechProvider,
    SpeechProvider,
    SpeechProviderError,
)
from app.speech.service import SpeechService

logger = logging.getLogger(__name__)

_kokoro_provider: SpeechProvider | None = None
_kokoro_provider_voice: str | None = None
_kokoro_provider_speed: float | None = None


def get_local_speech_provider() -> SpeechProvider | None:
    global _kokoro_provider, _kokoro_provider_speed, _kokoro_provider_voice
    if not settings.get_local_tts_enabled():
        return None
    if settings.get_local_tts_provider() != "kokoro":
        return None
    voice = settings.get_local_tts_voice()
    speed = settings.get_local_tts_speed()
    if (
        _kokoro_provider is None
        or _kokoro_provider_voice != voice
        or _kokoro_provider_speed != speed
    ):
        _kokoro_provider = KokoroSpeechProvider(
            voice=voice,
            speed=speed,
        )
        _kokoro_provider_voice = voice
        _kokoro_provider_speed = speed
    return _kokoro_provider


def get_openai_speech_provider() -> SpeechProvider | None:
    api_key = settings.get_openai_tts_api_key()
    if not api_key:
        return None
    try:
        return OpenAISpeechProvider(
            api_key=api_key,
            base_url=settings.get_openai_tts_base_url(),
            model=settings.get_openai_tts_model(),
            voice=settings.get_openai_tts_voice(),
            speed=settings.get_openai_tts_speed(),
            timeout_seconds=settings.get_openai_timeout_seconds(),
        )
    except SpeechProviderError as exc:
        logger.warning(
            "Speech provider unavailable",
            extra={"provider_error_code": exc.code},
        )
        return None


def get_speech_provider() -> SpeechProvider | None:
    providers = tuple(
        (name, provider)
        for name, provider in (
            ("kokoro", get_local_speech_provider()),
            ("openai", get_openai_speech_provider()),
        )
        if provider is not None
    )
    if not providers:
        logger.info(
            "Speech provider unavailable",
            extra={"speech_provider": "unavailable"},
        )
        return None
    return FallbackSpeechProvider(providers)


def get_speech_service(
    provider: Annotated[SpeechProvider | None, Depends(get_speech_provider)],
) -> SpeechService:
    return SpeechService(provider)


SpeechServiceDep = Annotated[SpeechService, Depends(get_speech_service)]

__all__ = [
    "get_local_speech_provider",
    "get_openai_speech_provider",
    "get_speech_provider",
    "get_speech_service",
    "SpeechServiceDep",
]
