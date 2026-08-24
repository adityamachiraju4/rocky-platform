"""Ownership-scoped persistence for Notifications."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, notification: Notification) -> Notification:
        self._session.add(notification)
        await self._session.flush()
        return notification

    async def get_owned(
        self, user_id: uuid.UUID, notification_id: uuid.UUID
    ) -> Notification | None:
        result = await self._session.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_source(
        self, user_id: uuid.UUID, source_type: str, source_id: uuid.UUID
    ) -> Notification | None:
        result = await self._session.execute(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.source_type == source_type,
                Notification.source_id == source_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_owned(
        self, user_id: uuid.UUID, status: str | None = None
    ) -> list[Notification]:
        statement = select(Notification).where(Notification.user_id == user_id)
        if status is not None:
            statement = statement.where(Notification.status == status)
        result = await self._session.execute(
            statement.order_by(Notification.created_at.desc(), Notification.id.desc())
        )
        return list(result.scalars().all())
