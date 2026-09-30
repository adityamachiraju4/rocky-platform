"""Dependency-injection wiring for transcription."""
from __future__ import annotations

import logging
import threading
from typing import Annotated

from fastapi import Depends

from app.core import settings
from app.transcription.local_whisper_provider import (
    LocalWhisperTranscriptionProvider,
)
from app.transcription.openai_provider import OpenAITranscriptionProvider
from app.transcription.provider import (
    TranscriptionProvider,
    TranscriptionProviderError,
)
from app.transcription.service import TranscriptionService

logger = logging.getLogger(__name__)

_local_whisper_provider: LocalWhisperTranscriptionProvider | None = None
_local_whisper_model: str | None = None
_local_whisper_device: str | None = None
_local_whisper_compute_type: str | None = None
_local_whisper_language: str | None = None
_local_whisper_provider_lock = threading.Lock()


def get_local_whisper_transcription_provider() -> TranscriptionProvider:
    global _local_whisper_compute_type
    global _local_whisper_device
    global _local_whisper_language
    global _local_whisper_model
    global _local_whisper_provider

    model = settings.get_local_whisper_model()
    device = settings.get_local_whisper_device()
    compute_type = settings.get_local_whisper_compute_type()
    language = settings.get_local_whisper_language()
    with _local_whisper_provider_lock:
        if (
            _local_whisper_provider is None
            or _local_whisper_model != model
            or _local_whisper_device != device
            or _local_whisper_compute_type != compute_type
            or _local_whisper_language != language
        ):
            _local_whisper_provider = LocalWhisperTranscriptionProvider(
                model_name=model,
                device=device,
                compute_type=compute_type,
                language=language,
            )
            _local_whisper_model = model
            _local_whisper_device = device
            _local_whisper_compute_type = compute_type
            _local_whisper_language = language
        return _local_whisper_provider


def get_existing_local_whisper_transcription_provider(
) -> LocalWhisperTranscriptionProvider | None:
    """Return the existing provider without creating it for the idle reaper."""

    with _local_whisper_provider_lock:
        return _local_whisper_provider


async def unload_existing_local_whisper_provider_if_idle(
    idle_seconds: float,
) -> bool:
    provider = get_existing_local_whisper_transcription_provider()
    if provider is None:
        return False
    return await provider.unload_if_idle(idle_seconds)


async def close_existing_local_whisper_provider() -> bool:
    global _local_whisper_compute_type
    global _local_whisper_device
    global _local_whisper_language
    global _local_whisper_model
    global _local_whisper_provider

    with _local_whisper_provider_lock:
        provider = _local_whisper_provider
        _local_whisper_provider = None
        _local_whisper_model = None
        _local_whisper_device = None
        _local_whisper_compute_type = None
        _local_whisper_language = None
    if provider is None:
        return False
    return await provider.close()


async def warm_local_whisper_transcription_provider() -> None:
    provider = get_local_whisper_transcription_provider()
    if isinstance(provider, LocalWhisperTranscriptionProvider):
        await provider.warm_up()


def get_openai_transcription_provider() -> TranscriptionProvider | None:
    api_key = settings.get_openai_transcription_api_key()
    if not api_key:
        return None
    try:
        return OpenAITranscriptionProvider(
            api_key=api_key,
            base_url=settings.get_openai_transcription_base_url(),
            model=settings.get_openai_transcription_model(),
            timeout_seconds=settings.get_openai_timeout_seconds(),
        )
    except TranscriptionProviderError as exc:
        logger.warning(
            "Transcription provider unavailable",
            extra={"provider_error_code": exc.code},
        )
        return None


def get_transcription_provider() -> TranscriptionProvider | None:
    provider_name = settings.get_transcription_provider_name()
    if provider_name == "local":
        provider = get_local_whisper_transcription_provider()
        logger.info(
            "Transcription provider configured",
            extra={"transcription_provider": "local_whisper"},
        )
        return provider
    if provider_name == "openai":
        provider = get_openai_transcription_provider()
        if provider is not None:
            logger.info(
                "Transcription provider configured",
                extra={"transcription_provider": "openai"},
            )
            return provider
        logger.info(
            "Transcription provider unavailable",
            extra={"transcription_provider": "openai"},
        )
        return None

    logger.warning(
        "Unsupported transcription provider configured",
        extra={"transcription_provider": provider_name},
    )
    provider = None
    if provider is None:
        logger.info(
            "Transcription provider unavailable",
            extra={"transcription_provider": "unavailable"},
        )
    return provider


def get_transcription_service(
    provider: Annotated[
        TranscriptionProvider | None,
        Depends(get_transcription_provider),
    ],
) -> TranscriptionService:
    return TranscriptionService(provider)


TranscriptionServiceDep = Annotated[
    TranscriptionService, Depends(get_transcription_service)
]

__all__ = [
    "get_local_whisper_transcription_provider",
    "get_existing_local_whisper_transcription_provider",
    "unload_existing_local_whisper_provider_if_idle",
    "close_existing_local_whisper_provider",
    "warm_local_whisper_transcription_provider",
    "get_openai_transcription_provider",
    "get_transcription_provider",
    "get_transcription_service",
    "TranscriptionServiceDep",
]
