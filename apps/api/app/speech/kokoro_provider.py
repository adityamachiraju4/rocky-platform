"""Local Kokoro speech provider.

Kokoro is loaded lazily and then reused for the lifetime of this provider
instance. The text never leaves the machine when this provider succeeds.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
import logging
import re
import threading
import time
import warnings

from app.speech.provider import SpeechAudio, SpeechProviderError

logger = logging.getLogger(__name__)

KOKORO_SAMPLE_RATE = 24000
KOKORO_REPO_ID = "hexgrad/Kokoro-82M"


class KokoroSpeechProvider:
    def __init__(self, *, voice: str, speed: float) -> None:
        self._voice = voice
        self._speed = speed
        self._pipeline = None
        self._lock = threading.Lock()
        self.last_error_code: str | None = None

    async def synthesize(self, text: str) -> SpeechAudio:
        self.last_error_code = None
        try:
            content = await asyncio.to_thread(self._synthesize_sync, text)
        except Exception as exc:  # noqa: BLE001 - provider fallback owns errors
            self.last_error_code = "request_failed"
            logger.warning(
                "Kokoro speech generation failed",
                extra={"provider_error_code": self.last_error_code},
            )
            raise SpeechProviderError(
                "Kokoro speech generation failed.",
                code=self.last_error_code,
            ) from exc

        if not content:
            self.last_error_code = "empty_audio"
            raise SpeechProviderError(
                "Kokoro speech generation returned empty audio.",
                code=self.last_error_code,
            )
        return SpeechAudio(content=content, media_type="audio/wav")

    async def warm_up(self) -> None:
        """Load the local pipeline without delaying API readiness."""
        started = time.perf_counter()
        await asyncio.to_thread(self._ensure_pipeline)
        logger.info(
            "Kokoro warm-up complete: elapsed_ms=%.1f",
            (time.perf_counter() - started) * 1000,
            extra={"speech_provider": "kokoro"},
        )

    def _synthesize_sync(self, text: str) -> bytes:
        pipeline = self._ensure_pipeline()
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

    def _ensure_pipeline(self):
        if self._pipeline is None:
            with self._lock:
                if self._pipeline is None:
                    started = time.perf_counter()
                    from kokoro import KPipeline

                    with warnings.catch_warnings():
                        # Kokoro 0.9.4 constructs its pinned model with these
                        # deprecated/no-op PyTorch options during startup.
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
                        self._pipeline = KPipeline(
                            lang_code=_voice_lang_code(self._voice),
                            repo_id=KOKORO_REPO_ID,
                        )
                    logger.info(
                        "Kokoro pipeline loaded: elapsed_ms=%.1f",
                        (time.perf_counter() - started) * 1000,
                        extra={"speech_provider": "kokoro"},
                    )
        return self._pipeline


def _voice_lang_code(voice: str) -> str:
    match = re.match(r"^[a-z]+", voice)
    if not match:
        return "a"
    prefix = match.group(0)
    return prefix[0]
