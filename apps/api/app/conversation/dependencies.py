"""Dependency-injection wiring for the Conversation orchestration layer.

Composes ConversationService from the Platform ``get_session`` provider. The
service itself constructs the peer capability services on that one session.
Dependency direction is Conversation -> Platform, and Conversation ->
{Projects, Tasks} services; nothing depends upward on Conversation.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session

from app.conversation.service import ConversationService


def get_conversation_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationService:
    return ConversationService(session)


ConversationServiceDep = Annotated[
    ConversationService, Depends(get_conversation_service)
]

__all__ = ["get_conversation_service", "ConversationServiceDep"]
