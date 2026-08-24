"""Authoritative Notifications creation, ownership, and lifecycle policy."""
from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import Clock, ensure_utc, system_clock
from app.models.notification import DISMISSED, READ, UNREAD, Notification
from app.models.user import User
from app.notifications.exceptions import (
    InvalidNotificationTransitionError,
    NotificationNotFoundError,
    NotificationSourceConflictError,
)
from app.notifications.repository import NotificationRepository
from app.notifications.schemas import NotificationCreate

VALID_STATUSES = frozenset({UNREAD, READ, DISMISSED})


class NotificationsService:
    def __init__(self, session: AsyncSession, *, clock: Clock = system_clock) -> None:
        self._session = session
        self._clock = clock
        self._notifications = NotificationRepository(session)

    async def create_notification(
        self, current_user: User, data: NotificationCreate
    ) -> Notification:
        return await self.create_for_user(current_user.id, data)

    async def create_for_user(
        self, user_id: uuid.UUID, data: NotificationCreate
    ) -> Notification:
        if (data.source_type is None) != (data.source_id is None):
            raise ValueError("source_type and source_id must be provided together.")
        if data.source_type and data.source_id:
            existing = await self._notifications.get_by_source(
                user_id, data.source_type, data.source_id
            )
            if existing is not None:
                if not self._matches(existing, data):
                    raise NotificationSourceConflictError(str(data.source_id))
                return existing

        notification = Notification(
            user_id=user_id,
            type=data.type,
            title=data.title.strip(),
            body=data.body.strip(),
            source_type=data.source_type,
            source_id=data.source_id,
            source_metadata=data.source_metadata,
        )
        try:
            await self._notifications.add(notification)
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            if data.source_type is None or data.source_id is None:
                raise
            existing = await self._notifications.get_by_source(
                user_id, data.source_type, data.source_id
            )
            if existing is None:
                raise
            if not self._matches(existing, data):
                raise NotificationSourceConflictError(str(data.source_id)) from None
            return existing
        await self._session.refresh(notification)
        return notification

    async def list_notifications(
        self, current_user: User, *, status: str | None = None
    ) -> list[Notification]:
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(f"Unknown notification status: {status}")
        return await self._notifications.list_owned(current_user.id, status)

    async def get_notification(
        self, current_user: User, notification_id: uuid.UUID
    ) -> Notification:
        notification = await self._notifications.get_owned(
            current_user.id, notification_id
        )
        if notification is None:
            raise NotificationNotFoundError(str(notification_id))
        return notification

    async def mark_read(
        self, current_user: User, notification_id: uuid.UUID
    ) -> Notification:
        notification = await self.get_notification(current_user, notification_id)
        if notification.status == DISMISSED:
            raise InvalidNotificationTransitionError(notification.status)
        if notification.status == UNREAD:
            notification.status = READ
            notification.read_at = ensure_utc(self._clock.now())
            await self._session.commit()
            await self._session.refresh(notification)
        return notification

    async def dismiss(
        self, current_user: User, notification_id: uuid.UUID
    ) -> Notification:
        notification = await self.get_notification(current_user, notification_id)
        if notification.status != DISMISSED:
            notification.status = DISMISSED
            notification.dismissed_at = ensure_utc(self._clock.now())
            await self._session.commit()
            await self._session.refresh(notification)
        return notification

    @staticmethod
    def _matches(notification: Notification, data: NotificationCreate) -> bool:
        return (
            notification.type == data.type
            and notification.title == data.title.strip()
            and notification.body == data.body.strip()
            and notification.source_metadata == data.source_metadata
        )
