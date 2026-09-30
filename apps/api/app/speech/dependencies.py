"""Dependency-injection wiring for the Speech rendering capability."""
from __future__ import annotations

import logging
import threading
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

_kokoro_provider: KokoroSpeechProvider | None = None
_kokoro_provider_voice: str | None = None
_kokoro_provider_speed: float | None = None
_kokoro_provider_lock = threading.Lock()


def get_local_speech_provider() -> SpeechProvider | None:
    global _kokoro_provider, _kokoro_provider_speed, _kokoro_provider_voice
    if not settings.get_local_tts_enabled():
        return None
    if settings.get_local_tts_provider() != "kokoro":
        return None
    voice = settings.get_local_tts_voice()
    speed = settings.get_local_tts_speed()
    with _kokoro_provider_lock:
        if (
            _kokoro_provider is None
            or _kokoro_provider_voice != voice
            or _kokoro_provider_speed != speed
        ):
            _kokoro_provider = KokoroSpeechProvider(
                voice=voice,
                speed=speed,
                startup_timeout_seconds=(
                    settings.get_voice_worker_startup_seconds()
                ),
                request_timeout_seconds=(
                    settings.get_voice_worker_request_seconds()
                ),
                shutdown_timeout_seconds=(
                    settings.get_voice_worker_shutdown_seconds()
                ),
            )
            _kokoro_provider_voice = voice
            _kokoro_provider_speed = speed
        return _kokoro_provider


def get_existing_local_speech_provider() -> KokoroSpeechProvider | None:
    """Return the existing provider without constructing one for the reaper."""

    with _kokoro_provider_lock:
        return _kokoro_provider


async def unload_existing_local_speech_provider_if_idle(
    idle_seconds: float,
) -> bool:
    provider = get_existing_local_speech_provider()
    if provider is None:
        return False
    return await provider.unload_if_idle(idle_seconds)


async def close_existing_local_speech_provider() -> bool:
    global _kokoro_provider
    global _kokoro_provider_speed
    global _kokoro_provider_voice

    with _kokoro_provider_lock:
        provider = _kokoro_provider
        _kokoro_provider = None
        _kokoro_provider_voice = None
        _kokoro_provider_speed = None
    if provider is None:
        return False
    return await provider.close()


async def warm_local_speech_provider() -> None:
    provider = get_local_speech_provider()
    if isinstance(provider, KokoroSpeechProvider):
        await provider.warm_up()


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
    "get_existing_local_speech_provider",
    "unload_existing_local_speech_provider_if_idle",
    "close_existing_local_speech_provider",
    "warm_local_speech_provider",
    "get_openai_speech_provider",
    "get_speech_provider",
    "get_speech_service",
    "SpeechServiceDep",
]
