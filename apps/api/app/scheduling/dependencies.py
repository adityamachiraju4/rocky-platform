"""Dependency wiring for services that schedule durable work."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session
from app.scheduling.service import SchedulingService


def get_scheduling_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SchedulingService:
    return SchedulingService(session)


SchedulingServiceDep = Annotated[
    SchedulingService, Depends(get_scheduling_service)
]
