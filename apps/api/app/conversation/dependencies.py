"""Dependency-injection wiring for the Conversation orchestration layer.

Composes ConversationService from the Platform ``get_session`` provider. The
service itself constructs the peer capability services on that one session.
Dependency direction is Conversation -> Platform, and Conversation ->
{Projects, Tasks} services; nothing depends upward on Conversation.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import settings
from app.core.dependencies import get_session
from app.live.dependencies import LiveIntelligenceServiceDep

from app.conversation.context import ConversationContextStore
from app.conversation.openai_provider import OpenAIUnderstandingProvider
from app.conversation.service import ConversationService
from app.conversation.understanding import (
    UnderstandingProvider,
    UnderstandingProviderError,
)

logger = logging.getLogger(__name__)


def get_understanding_provider() -> UnderstandingProvider | None:
    api_key = settings.get_openai_api_key()
    if not api_key:
        return None
    try:
        return OpenAIUnderstandingProvider(
            api_key=api_key,
            model=settings.get_openai_model(),
            timeout_seconds=settings.get_openai_timeout_seconds(),
        )
    except UnderstandingProviderError as exc:
        logger.warning(
            "Conversation understanding provider unavailable",
            extra={"provider_error_code": exc.code},
        )
        return None


def get_conversation_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    provider: Annotated[
        UnderstandingProvider | None,
        Depends(get_understanding_provider),
    ],
    live_service: LiveIntelligenceServiceDep,
) -> ConversationService:
    return ConversationService(
        session,
        understanding_provider=provider,
        live_service=live_service,
        context_store=ConversationContextStore(session),
    )


ConversationServiceDep = Annotated[
    ConversationService, Depends(get_conversation_service)
]

__all__ = [
    "get_conversation_service",
    "get_understanding_provider",
    "ConversationServiceDep",
]
