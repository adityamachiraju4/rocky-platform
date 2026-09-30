"""Local Kokoro speech provider.

Kokoro is loaded lazily, reused while voice is active, and may be detached
after an idle period. The text never leaves the machine when this provider
succeeds.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
import logging
import re
import time
import warnings
from collections.abc import Callable
from typing import Any

from app.speech.diagnostics import exception_details
from app.speech.provider import SpeechAudio, SpeechProviderError
from app.voice.lifecycle import IdleVoiceModel

logger = logging.getLogger(__name__)

KOKORO_SAMPLE_RATE = 24000
KOKORO_REPO_ID = "hexgrad/Kokoro-82M"


class KokoroSpeechProvider:
    def __init__(
        self,
        *,
        voice: str,
        speed: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._voice = voice
        self._speed = speed
        self._pipeline_lifecycle = IdleVoiceModel[Any](
            loader=self._load_pipeline,
            provider="kokoro",
            model=KOKORO_REPO_ID,
            monotonic=monotonic,
        )
        self.last_error_code: str | None = None

    @property
    def _pipeline(self) -> Any | None:
        return self._pipeline_lifecycle.resource

    @property
    def is_pipeline_loaded(self) -> bool:
        return self._pipeline_lifecycle.is_loaded

    @property
    def active_count(self) -> int:
        return self._pipeline_lifecycle.active_count

    async def synthesize(
        self, text: str, *, language: str = "en"
    ) -> SpeechAudio:
        self.last_error_code = None
        if _primary_language(language) != "en":
            self.last_error_code = "unsupported_language"
            raise SpeechProviderError(
                "Kokoro language is not available in this installation.",
                code=self.last_error_code,
                error_type="UnsupportedLanguage",
                reason=f"Configured Kokoro voice supports English, not {language!r}.",
            )
        try:
            content = await asyncio.to_thread(self._synthesize_sync, text)
        except Exception as exc:  # noqa: BLE001 - provider fallback owns errors
            self.last_error_code = "request_failed"
            error_type, reason = exception_details(exc)
            logger.warning(
                "Kokoro speech generation failed: error_type=%s reason=%s "
                "voice=%s",
                error_type,
                reason,
                self._voice,
                extra={
                    "speech_provider": "kokoro",
                    "provider_error_code": self.last_error_code,
                    "error_type": error_type,
                    "reason": reason,
                    "voice": self._voice,
                },
            )
            raise SpeechProviderError(
                "Kokoro speech generation failed.",
                code=self.last_error_code,
                error_type=error_type,
                reason=reason,
            ) from exc

        if not content:
            self.last_error_code = "empty_audio"
            raise SpeechProviderError(
                "Kokoro speech generation returned empty audio.",
                code=self.last_error_code,
                error_type="EmptyAudio",
                reason="Kokoro returned no audio segments.",
            )
        return SpeechAudio(content=content, media_type="audio/wav")

    async def warm_up(self) -> None:
        """Load the local pipeline without delaying API readiness."""
        started = time.perf_counter()
        await asyncio.to_thread(self._warm_up_sync)
        logger.info(
            "Kokoro warm-up complete: elapsed_ms=%.1f",
            (time.perf_counter() - started) * 1000,
            extra={"speech_provider": "kokoro"},
        )

    async def unload_if_idle(self, idle_seconds: float) -> bool:
        return await self._pipeline_lifecycle.unload_if_idle(idle_seconds)

    async def close(self) -> bool:
        return await self._pipeline_lifecycle.close()

    def _warm_up_sync(self) -> None:
        with self._pipeline_lifecycle.use():
            pass

    def _synthesize_sync(self, text: str) -> bytes:
        with self._pipeline_lifecycle.use() as pipeline:
            return self._synthesize_with_pipeline(pipeline, text)

    def _synthesize_with_pipeline(self, pipeline: Any, text: str) -> bytes:
        started = time.perf_counter()
        generator = pipeline(
            text,
            voice=self._voice,
            speed=self._speed,
        )

        segments = [audio for _, _, audio in generator]
        if not segments:
            return b""

        import numpy as np
        import soundfile as sf

        joined = np.concatenate(segments)
        output = BytesIO()
        sf.write(output, joined, KOKORO_SAMPLE_RATE, format="WAV")
        logger.info(
            "Kokoro synthesis complete: elapsed_ms=%.1f",
            (time.perf_counter() - started) * 1000,
            extra={"speech_provider": "kokoro"},
        )
        return output.getvalue()

    def _load_pipeline(self) -> Any:
        from kokoro import KPipeline

        with warnings.catch_warnings():
            # Kokoro 0.9.4 constructs its pinned model with these deprecated
            # or no-op PyTorch options during initialization.
            warnings.filterwarnings(
                "ignore",
                message="dropout option adds dropout.*",
                category=UserWarning,
                module=r"torch\.nn\.modules\.rnn",
            )
            warnings.filterwarnings(
                "ignore",
                message=r"`torch\.nn\.utils\.weight_norm` is deprecated.*",
                category=FutureWarning,
                module=r"torch\.nn\.utils\.weight_norm",
            )
            return KPipeline(
                lang_code=_voice_lang_code(self._voice),
                repo_id=KOKORO_REPO_ID,
            )


def _voice_lang_code(voice: str) -> str:
    match = re.match(r"^[a-z]+", voice)
    if not match:
        return "a"
    prefix = match.group(0)
    return prefix[0]


def _primary_language(value: str) -> str:
    return value.strip().lower().replace("_", "-").split("-", 1)[0] or "en"
