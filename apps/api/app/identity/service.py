"""Business logic layer for the Identity subsystem."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from .exceptions import (
    EmailAlreadyExistsError,
    PreferencesNotFoundError,
    UserNotFoundError,
)
from .models import Device, Preferences, User
from .repository import (
    DeviceRepository,
    PreferencesRepository,
    UserRepository,
)
from .schemas import PreferencesUpdate, UserCreate, UserUpdate


class IdentityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._prefs = PreferencesRepository(session)
        self._devices = DeviceRepository(session)

    # ---- Users ----
    async def create_user(self, data: UserCreate) -> User:
        if await self._users.get_by_email(data.email):
            raise EmailAlreadyExistsError(data.email)
        user = User(
            email=data.email,
            full_name=data.full_name,
            timezone=data.timezone,
            locale=data.locale,
        )
        await self._users.add(user)
        # Every user gets a default preferences row.
        await self._prefs.add(Preferences(user_id=user.id))
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

    # ---- Preferences ----
    async def get_preferences(self, user_id: uuid.UUID) -> Preferences:
        prefs = await self._prefs.get_by_user_id(user_id)
        if prefs is None:
            raise PreferencesNotFoundError(str(user_id))
        return prefs

    async def update_preferences(
        self, user_id: uuid.UUID, data: PreferencesUpdate
    ) -> Preferences:
        prefs = await self.get_preferences(user_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(prefs, field, value)
        await self._session.commit()
        await self._session.refresh(prefs)
        return prefs

    # ---- Devices ----
    async def list_devices(self, user_id: uuid.UUID) -> list[Device]:
        await self.get_user(user_id)  # validate existence
        return await self._devices.list_by_user_id(user_id)
