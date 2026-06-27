"""Unit tests for the Identity domain ORM models.

These tests use an in-memory SQLite database so they run without a live
PostgreSQL instance. They verify table creation, column defaults,
constraints, and the User -> Devices relationship.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import selectinload

from app.db.base import Base
from app.models import Device, User

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_user_defaults(session: AsyncSession) -> None:
    user = User(email="user@example.com", password_hash="hashed")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    assert user.id is not None
    assert user.is_active is True
    assert user.is_verified is False
    assert user.created_at is not None
    assert user.updated_at is not None


async def test_email_unique_constraint(session: AsyncSession) -> None:
    session.add(User(email="dup@example.com", password_hash="h1"))
    await session.commit()

    session.add(User(email="dup@example.com", password_hash="h2"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_user_devices_relationship(session: AsyncSession) -> None:
    user = User(email="rel@example.com", password_hash="h")
    session.add(user)
    await session.flush()

    session.add_all(
        [
            Device(
                user_id=user.id,
                device_name="iPhone",
                device_type="phone",
                platform="ios",
            ),
            Device(
                user_id=user.id,
                device_name="MacBook",
                device_type="laptop",
                platform="macos",
            ),
        ]
    )
    await session.commit()

    result = await session.execute(
        select(User)
        .where(User.id == user.id)
        .options(selectinload(User.devices))
    )
    loaded = result.scalar_one()
    assert len(loaded.devices) == 2
    assert {d.platform for d in loaded.devices} == {"ios", "macos"}
    assert all(d.user_id == user.id for d in loaded.devices)


async def test_device_back_populates_user(session: AsyncSession) -> None:
    user = User(email="back@example.com", password_hash="h")
    session.add(user)
    await session.flush()
    device = Device(user_id=user.id, device_name="Pixel", platform="android")
    session.add(device)
    await session.commit()

    result = await session.execute(
        select(Device)
        .where(Device.id == device.id)
        .options(selectinload(Device.user))
    )
    loaded = result.scalar_one()
    assert loaded.user.id == user.id
    assert loaded.user.email == "back@example.com"


async def test_cascade_delete_removes_devices(session: AsyncSession) -> None:
    user = User(email="cascade@example.com", password_hash="h")
    session.add(user)
    await session.flush()
    session.add(Device(user_id=user.id, device_name="Watch"))
    await session.commit()

    # Load with devices so the ORM cascade applies on delete.
    result = await session.execute(
        select(User)
        .where(User.id == user.id)
        .options(selectinload(User.devices))
    )
    loaded = result.scalar_one()
    await session.delete(loaded)
    await session.commit()

    remaining = await session.execute(select(Device))
    assert remaining.scalars().first() is None
