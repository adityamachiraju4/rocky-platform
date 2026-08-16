"""Database access layer for the Activity capability (read side).

Repositories perform persistence operations only — no business logic, no
transaction management. Writes go through
:class:`~app.activity.recorder.ActivityRecorder`; this module is the read side
consumed by the public read-only router.

Every read is scoped by ``user_id``. Another user's activity is never loaded
into memory, so ownership cannot be leaked by a later mistake.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity


class ActivityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_owned(
        self, user_id: uuid.UUID, activity_id: uuid.UUID
    ) -> Activity | None:
        result = await self._session.execute(
            select(Activity).where(
                Activity.id == activity_id,
                Activity.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_user_id(self, user_id: uuid.UUID) -> list[Activity]:
        result = await self._session.execute(
            select(Activity)
            .where(Activity.user_id == user_id)
            .order_by(Activity.created_at.desc())
        )
        return list(result.scalars().all())
