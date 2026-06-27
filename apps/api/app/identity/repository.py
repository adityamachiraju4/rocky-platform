"""Database access layer for the Identity subsystem.

Repositories perform persistence operations only — no business logic,
no transaction management, no domain decisions. They issue queries and
stage objects; committing is the caller's (service's) responsibility.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Device, Preferences, User


class UserRepository:
    """Persistence operations for :class:`User`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> User:
        self._session.add(user)
        await self._session.flush()
        return user

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()


class PreferencesRepository:
    """Persistence operations for :class:`Preferences`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, preferences: Preferences) -> Preferences:
        self._session.add(preferences)
        await self._session.flush()
        return preferences

    async def get_by_user_id(
        self, user_id: uuid.UUID
    ) -> Preferences | None:
        result = await self._session.execute(
            select(Preferences).where(Preferences.user_id == user_id)
        )
        return result.scalar_one_or_none()


class DeviceRepository:
    """Persistence operations for :class:`Device`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_user_id(self, user_id: uuid.UUID) -> list[Device]:
        result = await self._session.execute(
            select(Device).where(Device.user_id == user_id)
        )
        return list(result.scalars().all())
