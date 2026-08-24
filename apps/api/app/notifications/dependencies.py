"""Dependency wiring for Notifications."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session
from app.notifications.service import NotificationsService


def get_notifications_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NotificationsService:
    return NotificationsService(session)


NotificationsServiceDep = Annotated[
    NotificationsService, Depends(get_notifications_service)
]
