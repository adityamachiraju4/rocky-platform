"""Database access layer for the Identity subsystem.

Repositories perform persistence operations only — no business logic,
no transaction management. Committing is the service's responsibility.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.user import User


class UserRepository:
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


class DeviceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_user_id(self, user_id: uuid.UUID) -> list[Device]:
        result = await self._session.execute(
            select(Device).where(Device.user_id == user_id)
        )
        return list(result.scalars().all())

    async def get_by_user_and_client_id(
        self, user_id: uuid.UUID, client_id: uuid.UUID
    ) -> Device | None:
        result = await self._session.execute(
            select(Device).where(
                Device.user_id == user_id,
                Device.client_id == client_id,
            )
        )
        return result.scalar_one_or_none()

    async def add(self, device: Device) -> Device:
        self._session.add(device)
        await self._session.flush()
        return device
