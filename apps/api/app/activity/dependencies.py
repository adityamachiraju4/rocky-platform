"""Dependency-injection wiring for the Activity capability.

Capability-local DI: the read-side service is composed here from the Platform
``get_session`` provider. Platform never imports this module — dependency
direction is Capability -> Platform.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session

from .service import ActivityService


def get_activity_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ActivityService:
    return ActivityService(session)


ActivityServiceDep = Annotated[ActivityService, Depends(get_activity_service)]

__all__ = ["get_activity_service", "ActivityServiceDep"]
