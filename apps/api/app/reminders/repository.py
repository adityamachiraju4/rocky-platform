"""Ownership-scoped persistence for Reminders."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reminder import Reminder


class ReminderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, reminder: Reminder) -> Reminder:
        self._session.add(reminder)
        await self._session.flush()
        return reminder

    async def get(self, reminder_id: uuid.UUID) -> Reminder | None:
        return await self._session.get(Reminder, reminder_id)

    async def get_for_update(self, reminder_id: uuid.UUID) -> Reminder | None:
        result = await self._session.execute(
            select(Reminder)
            .where(Reminder.id == reminder_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_owned(
        self, user_id: uuid.UUID, reminder_id: uuid.UUID
    ) -> Reminder | None:
        result = await self._session.execute(
            select(Reminder).where(
                Reminder.id == reminder_id, Reminder.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def get_owned_for_update(
        self, user_id: uuid.UUID, reminder_id: uuid.UUID
    ) -> Reminder | None:
        result = await self._session.execute(
            select(Reminder)
            .where(Reminder.id == reminder_id, Reminder.user_id == user_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(
        self, user_id: uuid.UUID, key: str
    ) -> Reminder | None:
        result = await self._session.execute(
            select(Reminder).where(
                Reminder.user_id == user_id, Reminder.idempotency_key == key
            )
        )
        return result.scalar_one_or_none()

    async def list_owned(
        self, user_id: uuid.UUID, status: str | None = None
    ) -> list[Reminder]:
        statement = select(Reminder).where(Reminder.user_id == user_id)
        if status is not None:
            statement = statement.where(Reminder.status == status)
        result = await self._session.execute(
            statement.order_by(Reminder.due_at, Reminder.created_at)
        )
        return list(result.scalars().all())
