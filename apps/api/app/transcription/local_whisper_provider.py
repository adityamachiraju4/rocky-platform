"""Process-isolated local Whisper speech-to-text provider."""
from __future__ import annotations

import logging
import math
from multiprocessing.connection import Connection
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.transcription.language_stabilizer import stabilize_transcription_language
from app.transcription.provider import TranscriptionProviderError, TranscriptionResult
from app.voice.lifecycle import ManagedVoiceWorker, VoiceWorkerClient
from app.voice.worker import VoiceWorkerError, serve_voice_worker

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
_MAX_DIAGNOSTIC_SEGMENTS = 5
_MAX_DIAGNOSTIC_TEXT_CHARS = 80
_MAX_TRANSCRIPT_CHARS = 1_000_000
_MAX_LANGUAGE_CHARS = 32


def extension_for_content_type(content_type: str) -> str:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return _EXTENSIONS_BY_MEDIA_TYPE.get(media_type, ".audio")


def _safe_float(value: object) -> float | None:
    return float(value) if isinstance(value, (float, int)) else None


def _safe_segment_text(segment: Any) -> str:
    text = getattr(segment, "text", "")
    if not isinstance(text, str):
        return "<non-string>"
    rendered = " ".join(text.split())
    if len(rendered) <= _MAX_DIAGNOSTIC_TEXT_CHARS:
        return rendered
    return f"{rendered[:_MAX_DIAGNOSTIC_TEXT_CHARS]}..."


class LocalWhisperTranscriptionProvider:
    """Proxy local transcription requests to a lazy, reusable child process."""

    def __init__(
        self,
        *,
        model_name: str,
        device: str,
        compute_type: str,
        language: str,
        startup_timeout_seconds: float = 10.0,
        request_timeout_seconds: float = 300.0,
        shutdown_timeout_seconds: float = 5.0,
        monotonic: Callable[[], float] = time.monotonic,
        worker: VoiceWorkerClient | None = None,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._worker = worker or ManagedVoiceWorker(
            name="whisper",
            target=_whisper_worker_entry,
            startup_timeout_seconds=startup_timeout_seconds,
            request_timeout_seconds=request_timeout_seconds,
            shutdown_timeout_seconds=shutdown_timeout_seconds,
            monotonic=monotonic,
        )

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

    @property
    def is_model_loaded(self) -> bool:
        return self._worker.is_running

    @property
    def active_count(self) -> int:
        return self._worker.active_count

    async def transcribe(
        self, audio: bytes, *, filename: str, content_type: str
    ) -> TranscriptionResult:
        try:
            result = await self._worker.request(
                "transcribe",
                {
                    "audio": audio,
                    "filename": filename,
                    "content_type": content_type,
                    **self._configuration(),
                },
            )
            return _parse_transcription_result(result)
        except VoiceWorkerError as exc:
            code = (
                exc.code
                if exc.code
                in {"empty_transcription", "local_whisper_unavailable"}
                else "local_whisper_failed"
            )
            logger.warning(
                "Local Whisper transcription failed: error_type=%s "
                "reason=%s model=%s device=%s compute_type=%s language=%s "
                "audio_filename=%s content_type=%s",
                exc.error_type,
                exc.reason,
                self._model_name,
                self._device,
                self._compute_type,
                self._language,
                filename,
                content_type,
                extra={
                    "provider_error_code": code,
                    "worker_error_code": exc.code,
                },
            )
            message = (
                "Local Whisper transcription returned empty text."
                if code == "empty_transcription"
                else "Local Whisper transcription failed."
            )
            raise TranscriptionProviderError(message, code=code) from exc

    async def warm_up(self) -> None:
        """Warm only the child process; never import Whisper in the API process."""

        started = time.perf_counter()
        try:
            await self._worker.request("warmup", self._configuration())
        except VoiceWorkerError as exc:
            raise TranscriptionProviderError(
                "Local Whisper warm-up failed.",
                code="local_whisper_failed",
            ) from exc
        logger.info(
            "Local Whisper worker warm-up complete: elapsed_ms=%.1f model=%s",
            (time.perf_counter() - started) * 1000,
            self._model_name,
            extra={"transcription_provider": "local_whisper"},
        )

    async def unload_if_idle(self, idle_seconds: float) -> bool:
        return await self._worker.unload_if_idle(idle_seconds)

    async def close(self) -> bool:
        return await self._worker.close()

    def _configuration(self) -> dict[str, object]:
        return {
            "model_name": self._model_name,
            "device": self._device,
            "compute_type": self._compute_type,
            "language": self._language,
        }


class _WhisperWorker:
    """Child-only owner of WhisperModel and temporary audio files."""

    def __init__(self) -> None:
        self._model: Any | None = None
        self._configuration: tuple[str, str, str, str] | None = None

    def handle(
        self,
        operation: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        configuration = self._parse_configuration(payload)
        if operation not in {"warmup", "transcribe"}:
            raise _protocol_error("Unsupported Whisper worker operation.")
        model = self._ensure_model(configuration)
        if operation == "warmup":
            return {}

        audio = payload.get("audio")
        filename = payload.get("filename")
        content_type = payload.get("content_type")
        if not isinstance(audio, bytes) or not audio:
            raise _protocol_error("Whisper audio must be non-empty bytes.")
        if not isinstance(filename, str) or not filename or len(filename) > 255:
            raise _protocol_error("Whisper filename is invalid.")
        if (
            not isinstance(content_type, str)
            or not content_type
            or len(content_type) > 255
        ):
            raise _protocol_error("Whisper content type is invalid.")

        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=extension_for_content_type(content_type),
                prefix="rocky-transcription-",
                delete=False,
            ) as temporary:
                temporary.write(audio)
                path = Path(temporary.name)
            return self._transcribe_file(
                model,
                path,
                language=configuration[3],
                model_name=configuration[0],
            )
        except VoiceWorkerError:
            raise
        except Exception as exc:  # noqa: BLE001 - typed child failure
            raise VoiceWorkerError(
                "Local Whisper transcription failed.",
                code="local_whisper_failed",
                error_type=exc.__class__.__name__,
                reason=_safe_message(str(exc))
                or "The local transcription worker failed.",
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

    @staticmethod
    def _parse_configuration(
        payload: dict[str, object],
    ) -> tuple[str, str, str, str]:
        values: list[str] = []
        for key in ("model_name", "device", "compute_type", "language"):
            value = payload.get(key)
            if not isinstance(value, str) or not value or len(value) > 120:
                raise _protocol_error(f"Whisper field {key!r} is invalid.")
            values.append(value)
        return values[0], values[1], values[2], values[3]

    def _ensure_model(self, configuration: tuple[str, str, str, str]) -> Any:
        if self._model is not None:
            if self._configuration != configuration:
                raise _protocol_error("Whisper worker configuration changed.")
            return self._model
        model_name, device, compute_type, _ = configuration
        try:
            from faster_whisper import WhisperModel

            model = WhisperModel(
                model_name,
                device=device,
                compute_type=compute_type,
            )
        except ImportError as exc:
            raise VoiceWorkerError(
                "faster-whisper is not installed.",
                code="local_whisper_unavailable",
                error_type="ImportError",
                reason="The local transcription runtime is unavailable.",
                fatal=True,
            ) from exc
        except Exception as exc:  # noqa: BLE001 - typed child failure
            raise VoiceWorkerError(
                "Local Whisper initialization failed.",
                code="local_whisper_failed",
                error_type=exc.__class__.__name__,
                reason=_safe_message(str(exc))
                or "The local transcription model could not be initialized.",
                fatal=True,
            ) from exc
        self._model = model
        self._configuration = configuration
        return model

    def _transcribe_file(
        self,
        model: Any,
        path: Path,
        *,
        language: str,
        model_name: str,
    ) -> dict[str, object]:
        started = time.perf_counter()
        configured_language = None if language.strip().lower() == "auto" else language
        segments, info = model.transcribe(
            str(path),
            language=configured_language,
            task="transcribe",
        )
        segments = list(segments)
        audio_duration = _safe_float(getattr(info, "duration", None))
        audio_duration_after_vad = _safe_float(
            getattr(info, "duration_after_vad", None)
        )
        provider_language = getattr(info, "language", None)
        detected_language = configured_language or provider_language
        language_probability = getattr(info, "language_probability", None)
        raw_language = provider_language or configured_language
        raw_language_probability = language_probability

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
            english_duration = _safe_float(getattr(english_info, "duration", None))
            english_duration_after_vad = _safe_float(
                getattr(english_info, "duration_after_vad", None)
            )
            if self._average_log_probability(english_segments) >= (
                self._average_log_probability(segments) - _ENGLISH_SCORE_TOLERANCE
            ):
                segments = english_segments
                detected_language = "en"
                language_probability = getattr(
                    english_info, "language_probability", None
                )
                audio_duration = english_duration
                audio_duration_after_vad = english_duration_after_vad

        text = "".join(segment.text for segment in segments).strip()
        segment_texts = [_safe_segment_text(segment) for segment in segments]
        combined_text_length = len(text)
        blank_after_strip = not bool(text)
        normalized_language_probability = _safe_float(language_probability)
        normalized_raw_language_probability = _safe_float(
            raw_language_probability
        )
        stable_language = (
            configured_language
            if configured_language is not None
            else stabilize_transcription_language(
                text,
                detected_language,
                normalized_language_probability,
            )
        )
        logger.info(
            "Local Whisper inference complete: elapsed_ms=%.1f model=%s "
            "configured_language=%s transcript_language=%s "
            "detected_language=%s language_probability=%s raw_language=%s "
            "raw_language_probability=%s segment_count=%d "
            "combined_transcript_length=%d blank_after_strip=%s "
            "audio_duration=%s audio_duration_after_vad=%s",
            (time.perf_counter() - started) * 1000,
            model_name,
            configured_language or "auto",
            stable_language,
            detected_language,
            normalized_language_probability,
            raw_language,
            normalized_raw_language_probability,
            len(segments),
            combined_text_length,
            blank_after_strip,
            audio_duration,
            audio_duration_after_vad,
            extra={
                "transcription_provider": "local_whisper",
                "configured_language": configured_language or "auto",
                "transcript_language": stable_language,
                "detected_language": detected_language,
                "language_probability": normalized_language_probability,
                "raw_language": raw_language,
                "raw_language_probability": normalized_raw_language_probability,
                "segment_count": len(segments),
                "segment_texts": segment_texts[:_MAX_DIAGNOSTIC_SEGMENTS],
                "combined_transcript_length": combined_text_length,
                "blank_after_strip": blank_after_strip,
                "audio_duration": audio_duration,
                "audio_duration_after_vad": audio_duration_after_vad,
                "provider_return_type": type(segments).__name__,
                "provider_state": "empty_transcription"
                if blank_after_strip
                else "recognized",
            },
        )
        logger.info(
            "Local Whisper segments diagnostic: segment_count=%d "
            "segment_texts=%s",
            len(segments),
            segment_texts[:_MAX_DIAGNOSTIC_SEGMENTS],
            extra={
                "transcription_provider": "local_whisper",
                "segment_count": len(segments),
                "segment_texts": segment_texts[:_MAX_DIAGNOSTIC_SEGMENTS],
            },
        )
        if not text:
            logger.info(
                "Local Whisper transcription considered empty: "
                "reason=blank_after_strip segment_count=%d "
                "combined_transcript_length=%d detected_language=%s "
                "language_probability=%s audio_duration=%s "
                "audio_duration_after_vad=%s",
                len(segments),
                combined_text_length,
                detected_language,
                normalized_language_probability,
                audio_duration,
                audio_duration_after_vad,
                extra={
                    "provider_error_code": "empty_transcription",
                    "transcription_provider": "local_whisper",
                    "reason": "blank_after_strip",
                },
            )
            raise VoiceWorkerError(
                "Local Whisper transcription returned empty text.",
                code="empty_transcription",
                error_type="EmptyTranscription",
                reason="The local transcription worker returned blank text.",
            )
        return {
            "text": text,
            "language": stable_language,
            "language_probability": normalized_language_probability,
            "raw_language": raw_language,
            "raw_language_probability": normalized_raw_language_probability,
        }

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


def _whisper_worker_entry(connection: Connection) -> None:
    worker = _WhisperWorker()
    serve_voice_worker(connection, worker.handle)


def _parse_transcription_result(
    result: dict[str, object],
) -> TranscriptionResult:
    text = result.get("text")
    if not isinstance(text, str) or not text or len(text) > _MAX_TRANSCRIPT_CHARS:
        raise _malformed_response()
    language = _optional_string(result.get("language"))
    raw_language = _optional_string(result.get("raw_language"))
    language_probability = _optional_probability(
        result.get("language_probability")
    )
    raw_language_probability = _optional_probability(
        result.get("raw_language_probability")
    )
    return TranscriptionResult(
        text=text,
        language=language,
        language_probability=language_probability,
        raw_language=raw_language,
        raw_language_probability=raw_language_probability,
    )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > _MAX_LANGUAGE_CHARS:
        raise _malformed_response()
    return value


def _optional_probability(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _malformed_response()
    probability = float(value)
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise _malformed_response()
    return probability


def _malformed_response() -> VoiceWorkerError:
    return VoiceWorkerError(
        "Whisper worker returned invalid transcription metadata.",
        code="worker_protocol_error",
        error_type="MalformedResponse",
        reason="The local transcription worker returned invalid metadata.",
    )


def _protocol_error(reason: str) -> VoiceWorkerError:
    return VoiceWorkerError(
        "Invalid Whisper worker request.",
        code="worker_protocol_error",
        error_type="MalformedRequest",
        reason=reason,
        fatal=True,
    )


def _safe_message(value: str) -> str:
    return " ".join(value.split())[:240]
