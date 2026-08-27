"""Local Whisper-backed speech-to-text provider."""
from __future__ import annotations

import asyncio
import logging
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from app.transcription.provider import TranscriptionProviderError, TranscriptionResult

logger = logging.getLogger(__name__)

_EXTENSIONS_BY_MEDIA_TYPE = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/x-wav": ".wav",
    "audio/aac": ".aac",
    "audio/m4a": ".m4a",
}

_ENGLISH_FALLBACK_LANGUAGES = frozenset(
    {"ca", "de", "es", "fr", "it", "nl", "pt"}
)
_LOW_LANGUAGE_CONFIDENCE = 0.65
_ENGLISH_SCORE_TOLERANCE = 0.15


def extension_for_content_type(content_type: str) -> str:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return _EXTENSIONS_BY_MEDIA_TYPE.get(media_type, ".audio")


class LocalWhisperTranscriptionProvider:
    """Run speech-to-text locally with faster-whisper.

    The faster-whisper model is loaded lazily on the first transcription and
    reused for the provider lifetime. A lock keeps concurrent first requests
    from loading duplicate model instances.
    """

    def __init__(
        self,
        *,
        model_name: str,
        device: str,
        compute_type: str,
        language: str,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._model: Any | None = None
        self._model_lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def device(self) -> str:
        return self._device

    @property
    def compute_type(self) -> str:
        return self._compute_type

    @property
    def language(self) -> str:
        return self._language

    async def transcribe(
        self, audio: bytes, *, filename: str, content_type: str
    ) -> TranscriptionResult:
        suffix = extension_for_content_type(content_type)
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=suffix,
                prefix="rocky-transcription-",
                delete=False,
            ) as temp:
                temp.write(audio)
                path = Path(temp.name)
            return await asyncio.to_thread(self._transcribe_file, path)
        except TranscriptionProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - caller maps provider failure
            logger.warning(
                "Local Whisper transcription failed: error_type=%s "
                "message=%s model=%s device=%s compute_type=%s language=%s "
                "audio_filename=%s content_type=%s",
                exc.__class__.__name__,
                _safe_message(str(exc)),
                self._model_name,
                self._device,
                self._compute_type,
                self._language,
                filename,
                content_type,
                extra={"provider_error_code": "local_whisper_failed"},
            )
            raise TranscriptionProviderError(
                "Local Whisper transcription failed.",
                code="local_whisper_failed",
            ) from exc
        finally:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:  # pragma: no cover - best effort
                    logger.warning(
                        "Failed to delete temporary transcription audio: "
                        "error_type=%s message=%s",
                        exc.__class__.__name__,
                        _safe_message(str(exc)),
                        extra={
                            "provider_error_code": "temp_audio_delete_failed"
                        },
                    )

    async def warm_up(self) -> None:
        """Load the model off the event loop before the first voice request."""
        started = time.perf_counter()
        await asyncio.to_thread(self._get_model)
        logger.info(
            "Local Whisper warm-up complete: elapsed_ms=%.1f model=%s",
            (time.perf_counter() - started) * 1000,
            self._model_name,
            extra={"transcription_provider": "local_whisper"},
        )

    def _transcribe_file(self, path: Path) -> TranscriptionResult:
        model = self._get_model()
        started = time.perf_counter()
        configured_language = (
            None if self._language.strip().lower() == "auto" else self._language
        )
        segments, info = model.transcribe(
            str(path),
            language=configured_language,
            task="transcribe",
        )
        segments = list(segments)
        detected_language = getattr(info, "language", None) or configured_language
        language_probability = getattr(info, "language_probability", None)

        if self._should_try_english_fallback(
            configured_language,
            detected_language,
            language_probability,
        ):
            english_segments, english_info = model.transcribe(
                str(path),
                language="en",
                task="transcribe",
            )
            english_segments = list(english_segments)
            if self._average_log_probability(english_segments) >= (
                self._average_log_probability(segments) - _ENGLISH_SCORE_TOLERANCE
            ):
                segments = english_segments
                detected_language = "en"
                language_probability = getattr(
                    english_info, "language_probability", None
                )

        text = "".join(segment.text for segment in segments).strip()
        logger.info(
            "Local Whisper inference complete: elapsed_ms=%.1f model=%s",
            (time.perf_counter() - started) * 1000,
            self._model_name,
            extra={"transcription_provider": "local_whisper"},
        )
        if not text:
            raise TranscriptionProviderError(
                "Local Whisper transcription returned empty text.",
                code="empty_transcription",
            )
        return TranscriptionResult(
            text=text,
            language=detected_language,
            language_probability=(
                float(language_probability)
                if isinstance(language_probability, (float, int))
                else None
            ),
        )

    @staticmethod
    def _should_try_english_fallback(
        configured_language: str | None,
        detected_language: str | None,
        language_probability: object,
    ) -> bool:
        return (
            configured_language is None
            and detected_language in _ENGLISH_FALLBACK_LANGUAGES
            and isinstance(language_probability, (float, int))
            and language_probability < _LOW_LANGUAGE_CONFIDENCE
        )

    @staticmethod
    def _average_log_probability(segments: list[Any]) -> float:
        scores = [
            float(segment.avg_logprob)
            for segment in segments
            if isinstance(getattr(segment, "avg_logprob", None), (float, int))
        ]
        return sum(scores) / len(scores) if scores else float("-inf")

    def _get_model(self) -> Any:
        with self._model_lock:
            if self._model is None:
                started = time.perf_counter()
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:  # pragma: no cover - env guard
                    raise TranscriptionProviderError(
                        "faster-whisper is not installed.",
                        code="local_whisper_unavailable",
                    ) from exc
                logger.info(
                    "Loading local Whisper transcription model: "
                    "model=%s device=%s compute_type=%s",
                    self._model_name,
                    self._device,
                    self._compute_type,
                    extra={"transcription_provider": "local_whisper"},
                )
                self._model = WhisperModel(
                    self._model_name,
                    device=self._device,
                    compute_type=self._compute_type,
                )
                logger.info(
                    "Local Whisper model loaded: elapsed_ms=%.1f model=%s",
                    (time.perf_counter() - started) * 1000,
                    self._model_name,
                    extra={"transcription_provider": "local_whisper"},
                )
        return self._model


def _safe_message(value: str) -> str:
    return " ".join(value.split())[:240]
