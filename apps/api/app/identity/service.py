"""Business logic layer for the Identity subsystem."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.device import Device
from app.models.user import User

from .exceptions import EmailAlreadyExistsError, UserNotFoundError
from .repository import DeviceRepository, UserRepository
from .schemas import UserCreate, UserUpdate


class IdentityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._devices = DeviceRepository(session)

    async def create_user(self, data: UserCreate) -> User:
        if await self._users.get_by_email(data.email):
            raise EmailAlreadyExistsError(data.email)
        user = User(
            email=data.email,
            full_name=data.full_name,
            timezone=data.timezone,
            password_hash=hash_password(data.password),
        )
        await self._users.add(user)
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError(str(user_id))
        return user

    async def update_user(
        self, user_id: uuid.UUID, data: UserUpdate
    ) -> User:
        user = await self.get_user(user_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(user, field, value)
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def list_devices(self, user_id: uuid.UUID) -> list[Device]:
        await self.get_user(user_id)
        return await self._devices.list_by_user_id(user_id)
