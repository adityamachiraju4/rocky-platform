from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.dependencies import get_session
from app.db.base import Base
from app.main import app as fastapi_app
from app.models.notification import Notification
from app.models.user import User
from app.notifications.exceptions import NotificationSourceConflictError
from app.notifications.schemas import NotificationCreate
from app.notifications.service import NotificationsService
import app.models  # noqa: F401

SECRET = "test-secret-key"
PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    monkeypatch.setenv("REFRESH_TOKEN_PEPPER", "pepper")


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    fastapi_app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=fastapi_app), base_url="http://testserver"
        ) as client:
            yield client, maker
    finally:
        fastapi_app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


async def auth(
    client: httpx.AsyncClient, maker: async_sessionmaker
) -> tuple[dict[str, str], User]:
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    created = await client.post(
        "/identity/users",
        json={"email": email, "password": PASSWORD, "full_name": "Ada"},
    )
    user_id = uuid.UUID(created.json()["id"])
    async with maker() as session:
        await session.execute(
            update(User).where(User.id == user_id).values(is_verified=True)
        )
        await session.commit()
        user = await session.get(User, user_id)
    login = await client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}, user


async def create_internal(
    maker: async_sessionmaker, user_id: uuid.UUID, title: str, source_id: uuid.UUID
) -> uuid.UUID:
    async with maker() as session:
        user = await session.get(User, user_id)
        item = await NotificationsService(session).create_notification(
            user,
            NotificationCreate(
                type="reminder",
                title=title,
                body=title,
                source_type="reminder",
                source_id=source_id,
            ),
        )
        return item.id


@pytest.mark.asyncio
async def test_internal_create_is_source_idempotent_and_conflict_safe(ctx) -> None:
    client, maker = ctx
    _, user = await auth(client, maker)
    source_id = uuid.uuid4()
    first = await create_internal(maker, user.id, "Call Ramesh", source_id)
    second = await create_internal(maker, user.id, "Call Ramesh", source_id)
    assert first == second

    async with maker() as session:
        stored_user = await session.get(User, user.id)
        with pytest.raises(NotificationSourceConflictError):
            await NotificationsService(session).create_notification(
                stored_user,
                NotificationCreate(
                    type="reminder",
                    title="Different",
                    body="Different",
                    source_type="reminder",
                    source_id=source_id,
                ),
            )


@pytest.mark.asyncio
async def test_list_is_owned_newest_first_and_get_hides_other_users(ctx) -> None:
    client, maker = ctx
    mine, me = await auth(client, maker)
    theirs, other = await auth(client, maker)
    older = await create_internal(maker, me.id, "Older", uuid.uuid4())
    newer = await create_internal(maker, me.id, "Newer", uuid.uuid4())
    foreign = await create_internal(maker, other.id, "Private", uuid.uuid4())
    async with maker() as session:
        await session.execute(
            update(Notification)
            .where(Notification.id == older)
            .values(created_at=datetime.now(timezone.utc) - timedelta(hours=1))
        )
        await session.commit()

    listed = await client.get("/notifications", headers=mine)
    assert [item["id"] for item in listed.json()] == [str(newer), str(older)]
    assert (await client.get(f"/notifications/{newer}", headers=mine)).status_code == 200
    assert (await client.get(f"/notifications/{foreign}", headers=mine)).status_code == 404
    assert (await client.get(f"/notifications/{newer}", headers=theirs)).status_code == 404


@pytest.mark.asyncio
async def test_read_and_dismiss_are_idempotent_with_explicit_timestamps(ctx) -> None:
    client, maker = ctx
    headers, user = await auth(client, maker)
    item_id = await create_internal(maker, user.id, "Read me", uuid.uuid4())

    read = await client.patch(
        f"/notifications/{item_id}", json={"status": "read"}, headers=headers
    )
    assert read.status_code == 200
    assert read.json()["status"] == "read"
    assert read.json()["read_at"] is not None
    read_again = await client.patch(
        f"/notifications/{item_id}", json={"status": "read"}, headers=headers
    )
    assert read_again.json()["read_at"] == read.json()["read_at"]

    dismissed = await client.patch(
        f"/notifications/{item_id}",
        json={"status": "dismissed"},
        headers=headers,
    )
    assert dismissed.json()["status"] == "dismissed"
    assert dismissed.json()["dismissed_at"] is not None
    dismissed_again = await client.patch(
        f"/notifications/{item_id}",
        json={"status": "dismissed"},
        headers=headers,
    )
    assert dismissed_again.json()["dismissed_at"] == dismissed.json()["dismissed_at"]
    invalid = await client.patch(
        f"/notifications/{item_id}", json={"status": "read"}, headers=headers
    )
    assert invalid.status_code == 409
