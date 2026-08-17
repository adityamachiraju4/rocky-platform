"""HTTP routing for neural speech rendering."""
from __future__ import annotations

import logging

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
    try:
        audio = await service.synthesize(payload.text)
    except SpeechUnavailableError as exc:
        logger.warning("Speech synthesis unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Speech is unavailable.",
        ) from exc

    return Response(content=audio.content, media_type=audio.media_type)
