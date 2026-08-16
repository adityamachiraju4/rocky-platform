"""Business logic layer for the Activity capability (read side).

The service owns transactions and owns ownership policy: every method takes the
authenticated :class:`User` and scopes all reads by that user's id. No method
accepts a caller-supplied owner identifier, and there is no create method — the
public surface is read-only. Emission is the recorder's job, called by the
domain services that own the mutating transaction.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.user import User

from .exceptions import ActivityNotFoundError
from .repository import ActivityRepository


class ActivityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._activities = ActivityRepository(session)

    async def list_activities(self, current_user: User) -> list[Activity]:
        return await self._activities.list_by_user_id(current_user.id)

    async def get_activity(
        self, current_user: User, activity_id: uuid.UUID
    ) -> Activity:
        activity = await self._activities.get_owned(
            current_user.id, activity_id
        )
        if activity is None:
            raise ActivityNotFoundError(str(activity_id))
        return activity
