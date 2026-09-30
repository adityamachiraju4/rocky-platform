"""Single application-level idle reaper for existing local voice providers."""
from __future__ import annotations

import asyncio
import logging

from app.speech.dependencies import unload_existing_local_speech_provider_if_idle
from app.transcription.dependencies import (
    unload_existing_local_whisper_provider_if_idle,
)

logger = logging.getLogger(__name__)


async def reap_idle_voice_models(*, idle_seconds: float) -> tuple[bool, bool]:
    """Check existing providers without constructing providers or models."""

    results = await asyncio.gather(
        unload_existing_local_whisper_provider_if_idle(idle_seconds),
        unload_existing_local_speech_provider_if_idle(idle_seconds),
        return_exceptions=True,
    )
    unloaded: list[bool] = []
    for provider, result in zip(("whisper", "kokoro"), results, strict=True):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            logger.warning(
                "voice_model_reaper_check_failed",
                extra={
                    "voice_provider": provider,
                    "error_type": result.__class__.__name__,
                },
            )
            unloaded.append(False)
        else:
            unloaded.append(result)
    return unloaded[0], unloaded[1]


async def run_voice_model_reaper(
    *,
    idle_seconds: float,
    interval_seconds: float,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await reap_idle_voice_models(idle_seconds=idle_seconds)
