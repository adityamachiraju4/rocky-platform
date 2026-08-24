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
from app.models.activity import Activity
from app.models.note import Note
from app.models.user import User
import app.models  # noqa: F401

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "test-secret")
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
) -> tuple[dict[str, str], uuid.UUID]:
    email = f"note-{uuid.uuid4().hex[:8]}@example.com"
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
    login = await client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}, user_id


@pytest.mark.asyncio
async def test_notes_require_auth(ctx) -> None:
    client, _ = ctx
    assert (await client.get("/notes")).status_code == 401
    assert (
        await client.post("/notes", json={"title": "Private", "content": "x"})
    ).status_code == 401


@pytest.mark.asyncio
async def test_create_records_activity_and_defaults_active(ctx) -> None:
    client, maker = ctx
    headers, user_id = await auth(client, maker)
    response = await client.post(
        "/notes",
        json={"title": "Launch ideas", "content": "Invite design partners."},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["status"] == "active"
    assert response.json()["archived_at"] is None

    async with maker() as session:
        activity = (
            await session.execute(
                select(Activity).where(
                    Activity.user_id == user_id,
                    Activity.event_type == "note.created",
                )
            )
        ).scalar_one()
        assert activity.payload == {"title": "Launch ideas"}


@pytest.mark.asyncio
async def test_list_defaults_active_and_orders_newest_updated_first(ctx) -> None:
    client, maker = ctx
    headers, _ = await auth(client, maker)
    older = await client.post("/notes", json={"title": "Older"}, headers=headers)
    newer = await client.post("/notes", json={"title": "Newer"}, headers=headers)
    async with maker() as session:
        await session.execute(
            update(Note)
            .where(Note.id == uuid.UUID(older.json()["id"]))
            .values(updated_at=datetime.now(timezone.utc) - timedelta(hours=1))
        )
        await session.commit()

    listed = await client.get("/notes", headers=headers)
    assert [item["title"] for item in listed.json()] == ["Newer", "Older"]


@pytest.mark.asyncio
async def test_get_and_update_are_owner_scoped(ctx) -> None:
    client, maker = ctx
    mine, _ = await auth(client, maker)
    theirs, _ = await auth(client, maker)
    created = await client.post(
        "/notes", json={"title": "Mine", "content": "first"}, headers=mine
    )
    note_id = created.json()["id"]
    assert (await client.get(f"/notes/{note_id}", headers=mine)).status_code == 200
    assert (await client.get(f"/notes/{note_id}", headers=theirs)).status_code == 404
    assert (
        await client.patch(
            f"/notes/{note_id}", json={"content": "stolen"}, headers=theirs
        )
    ).status_code == 404

    updated = await client.patch(
        f"/notes/{note_id}",
        json={"title": "Mine updated", "content": "second"},
        headers=mine,
    )
    assert updated.json()["title"] == "Mine updated"
    assert updated.json()["content"] == "second"

    async with maker() as session:
        events = list(
            (
                await session.execute(
                    select(Activity).where(
                        Activity.entity_id == uuid.UUID(note_id),
                        Activity.event_type == "note.updated",
                    )
                )
            ).scalars()
        )
        assert len(events) == 1
        assert events[0].payload["title"] == "Mine updated"


@pytest.mark.asyncio
async def test_archive_filter_idempotency_and_irreversible_lifecycle(ctx) -> None:
    client, maker = ctx
    headers, _ = await auth(client, maker)
    created = await client.post(
        "/notes", json={"title": "Archive me", "content": "done"}, headers=headers
    )
    note_id = created.json()["id"]
    archived = await client.patch(
        f"/notes/{note_id}", json={"status": "archived"}, headers=headers
    )
    assert archived.json()["status"] == "archived"
    assert archived.json()["archived_at"] is not None
    repeated = await client.patch(
        f"/notes/{note_id}", json={"status": "archived"}, headers=headers
    )
    assert repeated.json()["archived_at"] == archived.json()["archived_at"]
    assert (await client.get("/notes", headers=headers)).json() == []
    archived_list = await client.get("/notes?status=archived", headers=headers)
    assert [item["id"] for item in archived_list.json()] == [note_id]
    invalid = await client.patch(
        f"/notes/{note_id}", json={"content": "changed"}, headers=headers
    )
    assert invalid.status_code == 409

    async with maker() as session:
        events = list(
            (
                await session.execute(
                    select(Activity).where(
                        Activity.entity_id == uuid.UUID(note_id),
                        Activity.event_type == "note.archived",
                    )
                )
            ).scalars()
        )
        assert len(events) == 1


@pytest.mark.asyncio
async def test_update_rejects_null_plain_text_fields(ctx) -> None:
    client, maker = ctx
    headers, _ = await auth(client, maker)
    created = await client.post("/notes", json={"title": "Valid"}, headers=headers)
    for payload in ({"title": None}, {"content": None}):
        response = await client.patch(
            f"/notes/{created.json()['id']}", json=payload, headers=headers
        )
        assert response.status_code == 422
