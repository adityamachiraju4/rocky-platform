"""Process-isolated local Kokoro speech provider."""
from __future__ import annotations

from io import BytesIO
import logging
from multiprocessing.connection import Connection
import re
import time
import warnings
from collections.abc import Callable
from typing import Any

from app.speech.diagnostics import exception_details
from app.speech.provider import SpeechAudio, SpeechProviderError
from app.voice.lifecycle import ManagedVoiceWorker, VoiceWorkerClient
from app.voice.worker import VoiceWorkerError, serve_voice_worker

logger = logging.getLogger(__name__)

KOKORO_SAMPLE_RATE = 24000
KOKORO_REPO_ID = "hexgrad/Kokoro-82M"
_MAX_WORKER_AUDIO_BYTES = 64 * 1024 * 1024


class KokoroSpeechProvider:
    """Proxy local speech requests to a lazy, reusable child process."""

    def __init__(
        self,
        *,
        voice: str,
        speed: float,
        startup_timeout_seconds: float = 10.0,
        request_timeout_seconds: float = 300.0,
        shutdown_timeout_seconds: float = 5.0,
        monotonic: Callable[[], float] = time.monotonic,
        worker: VoiceWorkerClient | None = None,
    ) -> None:
        self._voice = voice
        self._speed = speed
        self._worker = worker or ManagedVoiceWorker(
            name="kokoro",
            target=_kokoro_worker_entry,
            startup_timeout_seconds=startup_timeout_seconds,
            request_timeout_seconds=request_timeout_seconds,
            shutdown_timeout_seconds=shutdown_timeout_seconds,
            monotonic=monotonic,
        )
        self.last_error_code: str | None = None

    @property
    def is_pipeline_loaded(self) -> bool:
        return self._worker.is_running

    @property
    def active_count(self) -> int:
        return self._worker.active_count

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
            result = await self._worker.request(
                "synthesize",
                {
                    "text": text,
                    "language": language,
                    "voice": self._voice,
                    "speed": self._speed,
                },
            )
            content = result.get("content")
            media_type = result.get("media_type")
            if (
                not isinstance(content, bytes)
                or not content
                or len(content) > _MAX_WORKER_AUDIO_BYTES
                or media_type != "audio/wav"
            ):
                raise VoiceWorkerError(
                    "Kokoro worker returned invalid audio.",
                    code="worker_protocol_error",
                    error_type="MalformedResponse",
                    reason="The local speech worker returned invalid audio.",
                )
        except VoiceWorkerError as exc:
            self.last_error_code = (
                exc.code
                if exc.code in {"empty_audio", "unsupported_language"}
                else "request_failed"
            )
            logger.warning(
                "Kokoro speech generation failed: error_type=%s reason=%s "
                "voice=%s",
                exc.error_type,
                exc.reason,
                self._voice,
                extra={
                    "speech_provider": "kokoro",
                    "provider_error_code": self.last_error_code,
                    "worker_error_code": exc.code,
                    "error_type": exc.error_type,
                    "reason": exc.reason,
                    "voice": self._voice,
                },
            )
            raise SpeechProviderError(
                "Kokoro speech generation failed.",
                code=self.last_error_code,
                error_type=exc.error_type,
                reason=exc.reason,
            ) from exc
        return SpeechAudio(content=content, media_type="audio/wav")

    async def warm_up(self) -> None:
        """Warm only the child process; never import Kokoro in the API process."""

        started = time.perf_counter()
        try:
            await self._worker.request(
                "warmup",
                {"voice": self._voice, "speed": self._speed},
            )
        except VoiceWorkerError as exc:
            raise SpeechProviderError(
                "Kokoro speech warm-up failed.",
                code="request_failed",
                error_type=exc.error_type,
                reason=exc.reason,
            ) from exc
        logger.info(
            "Kokoro worker warm-up complete: elapsed_ms=%.1f",
            (time.perf_counter() - started) * 1000,
            extra={"speech_provider": "kokoro"},
        )

    async def unload_if_idle(self, idle_seconds: float) -> bool:
        return await self._worker.unload_if_idle(idle_seconds)

    async def close(self) -> bool:
        return await self._worker.close()


class _KokoroWorker:
    """Child-only owner of KPipeline and its native runtime."""

    def __init__(self) -> None:
        self._pipeline: Any | None = None
        self._configuration: tuple[str, float] | None = None

    def handle(
        self,
        operation: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        voice = _required_string(payload, "voice", maximum=80)
        speed_value = payload.get("speed")
        if not isinstance(speed_value, (int, float)) or isinstance(
            speed_value, bool
        ):
            raise _protocol_error("Kokoro speed must be numeric.")
        speed = float(speed_value)
        if operation not in {"warmup", "synthesize"}:
            raise _protocol_error("Unsupported Kokoro worker operation.")
        pipeline = self._ensure_pipeline(voice=voice, speed=speed)
        if operation == "warmup":
            return {}
        text = _required_string(payload, "text", maximum=1200)
        language = _required_string(payload, "language", maximum=32)
        if _primary_language(language) != "en":
            raise VoiceWorkerError(
                "Unsupported Kokoro language.",
                code="unsupported_language",
                error_type="UnsupportedLanguage",
                reason="Kokoro supports English in this installation.",
            )
        try:
            content = self._synthesize(pipeline, text, voice=voice, speed=speed)
        except VoiceWorkerError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider boundary owns errors
            error_type, reason = exception_details(exc)
            raise VoiceWorkerError(
                "Kokoro synthesis failed.",
                code="request_failed",
                error_type=error_type,
                reason=reason,
            ) from exc
        if not content:
            raise VoiceWorkerError(
                "Kokoro returned empty audio.",
                code="empty_audio",
                error_type="EmptyAudio",
                reason="Kokoro returned no audio segments.",
            )
        return {"content": content, "media_type": "audio/wav"}

    def _ensure_pipeline(self, *, voice: str, speed: float) -> Any:
        configuration = (voice, speed)
        if self._pipeline is not None:
            if self._configuration != configuration:
                raise _protocol_error("Kokoro worker configuration changed.")
            return self._pipeline
        try:
            from kokoro import KPipeline

            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="dropout option adds dropout.*",
                    category=UserWarning,
                    module=r"torch\.nn\.modules\.rnn",
                )
                warnings.filterwarnings(
                    "ignore",
                    message=(
                        r"`torch\.nn\.utils\.weight_norm` is deprecated.*"
                    ),
                    category=FutureWarning,
                    module=r"torch\.nn\.utils\.weight_norm",
                )
                pipeline = KPipeline(
                    lang_code=_voice_lang_code(voice),
                    repo_id=KOKORO_REPO_ID,
                )
        except Exception as exc:  # noqa: BLE001 - typed child failure
            error_type, reason = exception_details(exc)
            raise VoiceWorkerError(
                "Kokoro initialization failed.",
                code="request_failed",
                error_type=error_type,
                reason=reason,
                fatal=True,
            ) from exc
        self._pipeline = pipeline
        self._configuration = configuration
        return pipeline

    @staticmethod
    def _synthesize(
        pipeline: Any,
        text: str,
        *,
        voice: str,
        speed: float,
    ) -> bytes:
        generator = pipeline(text, voice=voice, speed=speed)
        segments = [audio for _, _, audio in generator]
        if not segments:
            return b""

        import numpy as np
        import soundfile as sf

        joined = np.concatenate(segments)
        output = BytesIO()
        sf.write(output, joined, KOKORO_SAMPLE_RATE, format="WAV")
        return output.getvalue()


def _kokoro_worker_entry(connection: Connection) -> None:
    worker = _KokoroWorker()
    serve_voice_worker(connection, worker.handle)


def _required_string(
    payload: dict[str, object],
    key: str,
    *,
    maximum: int,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise _protocol_error(f"Kokoro field {key!r} is invalid.")
    return value


def _protocol_error(reason: str) -> VoiceWorkerError:
    return VoiceWorkerError(
        "Invalid Kokoro worker request.",
        code="worker_protocol_error",
        error_type="MalformedRequest",
        reason=reason,
        fatal=True,
    )


def _voice_lang_code(voice: str) -> str:
    match = re.match(r"^[a-z]+", voice)
    if not match:
        return "a"
    return match.group(0)[0]


def _primary_language(value: str) -> str:
    return value.strip().lower().replace("_", "-").split("-", 1)[0] or "en"
