"""HTTP routing for the Conversation orchestration layer.

The router contains NO logic: it authenticates via the same CurrentUserDep
every capability uses, delegates the whole turn to ConversationService, and
translates the one boundary failure — an unknown action — into a 500-class
signal, since that can only mean a resolver bug (or, later, a rejected model
hallucination), never a client error. Honest non-executions (no match,
ambiguity) are NOT errors: they come back as 200 with executed=False.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, status

from app.auth.dependencies import CurrentUserDep

from app.conversation.dependencies import ConversationServiceDep
from app.conversation.exceptions import UnknownActionError
from app.conversation.schemas import ConversationRequest, ConversationResponse

router = APIRouter(prefix="/conversation", tags=["conversation"])
logger = logging.getLogger(__name__)


@router.post("", response_model=ConversationResponse)
async def converse(
    payload: ConversationRequest,
    current_user: CurrentUserDep,
    service: ConversationServiceDep,
) -> ConversationResponse:
    started = time.perf_counter()
    try:
        response = await service.handle(
            current_user,
            payload.message,
            timezone_name=payload.timezone,
            language=payload.language,
        )
        logger.info(
            "Conversation response selected: selected_response_language=%s",
            response.language,
            extra={"selected_response_language": response.language},
        )
        return response
    except UnknownActionError as exc:
        # The resolver proposed something outside the closed registry. This is
        # never the caller's fault; surface it as an internal error.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Resolver proposed an unknown action.",
        ) from exc
    finally:
        logger.info(
            "Conversation request complete: elapsed_ms=%.1f",
            (time.perf_counter() - started) * 1000,
        )
