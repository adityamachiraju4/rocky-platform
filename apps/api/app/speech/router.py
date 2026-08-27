"""HTTP routing for neural speech rendering."""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response

from app.auth.dependencies import CurrentUserDep
from app.speech.dependencies import SpeechServiceDep
from app.speech.schemas import SpeechRequest
from app.speech.service import SpeechUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/speech", tags=["speech"])


@router.post("")
async def synthesize_speech(
    payload: SpeechRequest,
    current_user: CurrentUserDep,
    service: SpeechServiceDep,
) -> Response:
    started = time.perf_counter()
    try:
        audio = await service.synthesize(payload.text, language=payload.language)
    except SpeechUnavailableError as exc:
        logger.warning("Speech synthesis unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Speech is unavailable.",
        ) from exc
    finally:
        logger.info(
            "Speech request complete: elapsed_ms=%.1f",
            (time.perf_counter() - started) * 1000,
        )

    headers = (
        {"X-Rocky-Speech-Provider": audio.provider}
        if audio.provider is not None
        else None
    )
    return Response(
        content=audio.content,
        media_type=audio.media_type,
        headers=headers,
    )
