"""HTTP routing for Rocky-native speech-to-text."""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, UploadFile, status

from app.auth.dependencies import CurrentUserDep
from app.transcription.dependencies import TranscriptionServiceDep
from app.transcription.schemas import TranscriptionResponse
from app.transcription.service import (
    MAX_TRANSCRIPTION_AUDIO_BYTES,
    TranscriptionInputError,
    TranscriptionNoSpeechError,
    TranscriptionUnavailableError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transcribe", tags=["transcription"])


@router.post("", response_model=TranscriptionResponse)
async def transcribe_audio(
    audio: UploadFile,
    current_user: CurrentUserDep,
    service: TranscriptionServiceDep,
) -> TranscriptionResponse:
    started = time.perf_counter()
    try:
        content = await audio.read(MAX_TRANSCRIPTION_AUDIO_BYTES + 1)
        result = await service.transcribe(
            content,
            filename=audio.filename or "audio",
            content_type=audio.content_type,
        )
    except TranscriptionInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except TranscriptionNoSpeechError as exc:
        logger.info("Transcription completed with no recognized speech.")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="I didn't catch that clearly. Could you say it again?",
        ) from exc
    except TranscriptionUnavailableError as exc:
        logger.warning("Transcription unavailable: reason=%s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Transcription is unavailable.",
        ) from exc
    finally:
        await audio.close()
        logger.info(
            "Transcription request complete: elapsed_ms=%.1f",
            (time.perf_counter() - started) * 1000,
        )

    return TranscriptionResponse(
        text=result.text,
        language=result.language,
        language_probability=result.language_probability,
        raw_language=result.raw_language,
        raw_language_probability=result.raw_language_probability,
    )
