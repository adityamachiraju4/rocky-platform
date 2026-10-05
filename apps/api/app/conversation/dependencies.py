"""Dependency-injection wiring for the Conversation orchestration layer.

Composes ConversationService from the Platform ``get_session`` provider. The
service itself constructs the peer capability services on that one session.
Dependency direction is Conversation -> Platform, and Conversation ->
{Projects, Tasks} services; nothing depends upward on Conversation.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import settings
from app.core.dependencies import get_session
from app.live.dependencies import LiveIntelligenceServiceDep
from app.intelligence.decision import DecisionPolicy, DecisionProvider
from app.intelligence.typesafe.client import TypeSafeClient
from app.intelligence.typesafe.provider import TypeSafeDecisionProvider

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
            base_url=settings.get_openai_base_url(),
        )
    except UnderstandingProviderError as exc:
        logger.warning(
            "Conversation understanding provider unavailable",
            extra={"provider_error_code": exc.code},
        )
        return None


def get_decision_provider() -> DecisionProvider | None:
    if not settings.get_typesafe_enabled():
        return None
    api_key = settings.get_typesafe_api_key()
    model = settings.get_typesafe_model()
    if not api_key or not model:
        logger.warning(
            "TypeSafe decision provider unavailable",
            extra={"provider_error_code": "typesafe_configuration_missing"},
        )
        return None
    return _cached_decision_provider(
        api_key, settings.get_typesafe_base_url(), model,
        settings.get_typesafe_timeout_seconds(),
    )


@lru_cache(maxsize=4)
def _cached_decision_provider(
    api_key: str, base_url: str, model: str, timeout_seconds: float
) -> DecisionProvider:
    return TypeSafeDecisionProvider(
        TypeSafeClient(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        ),
        model=model,
    )


def get_decision_policy() -> DecisionPolicy:
    enabled = settings.get_typesafe_enabled()
    return DecisionPolicy(
        route_enabled=enabled and settings.get_typesafe_route_enabled(),
        confirmation_enabled=(
            enabled and settings.get_typesafe_confirmation_enabled()
        ),
        plan_verification_enabled=(
            enabled and settings.get_typesafe_plan_verification_enabled()
        ),
        plan_verification_required=(
            enabled and settings.get_typesafe_plan_verification_required()
        ),
    )


def get_conversation_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    provider: Annotated[
        UnderstandingProvider | None,
        Depends(get_understanding_provider),
    ],
    decision_provider: Annotated[
        DecisionProvider | None,
        Depends(get_decision_provider),
    ],
    decision_policy: Annotated[
        DecisionPolicy,
        Depends(get_decision_policy),
    ],
    live_service: LiveIntelligenceServiceDep,
) -> ConversationService:
    return ConversationService(
        session,
        understanding_provider=provider,
        decision_provider=decision_provider,
        decision_policy=decision_policy,
        live_service=live_service,
        context_store=ConversationContextStore(session),
    )


ConversationServiceDep = Annotated[
    ConversationService, Depends(get_conversation_service)
]

__all__ = [
    "get_conversation_service",
    "get_understanding_provider",
    "get_decision_provider",
    "get_decision_policy",
    "ConversationServiceDep",
]
