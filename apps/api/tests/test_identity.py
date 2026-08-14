"""Identity capability API tests.

Hermetic: in-memory SQLite (aiosqlite), schema from Base.metadata, with the
Platform get_session provider overridden so no live DB creds are needed.
Transport is httpx ASGITransport driving the real app (mirrors test_lifecycle).
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.dependencies import get_session
from app.db.base import Base
from app.main import app as fastapi_app

import app.models  # noqa: F401  (populate Base.metadata before create_all)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    fastapi_app.dependency_overrides[get_session] = _override_get_session
    try:
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            yield ac
    finally:
        fastapi_app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


def _payload(email: str | None = None) -> dict:
    return {
        "email": email or f"user-{uuid.uuid4().hex[:8]}@example.com",
        "password": "correct horse battery",
        "full_name": "Ada Lovelace",
    }


@pytest.mark.asyncio
async def test_create_user_201_no_hash(client) -> None:
    resp = await client.post("/identity/users", json=_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"].endswith("@example.com")
    assert body["full_name"] == "Ada Lovelace"
    assert body["is_active"] is True
    assert body["is_verified"] is False
    assert "password" not in body
    assert "password_hash" not in body
    uuid.UUID(body["id"])


@pytest.mark.asyncio
async def test_duplicate_email_409(client) -> None:
    p = _payload("dupe@example.com")
    assert (await client.post("/identity/users", json=p)).status_code == 201
    assert (await client.post("/identity/users", json=p)).status_code == 409


@pytest.mark.asyncio
async def test_get_user_roundtrip_and_404(client) -> None:
    created = await client.post("/identity/users", json=_payload())
    uid = created.json()["id"]
    got = await client.get(f"/identity/users/{uid}")
    assert got.status_code == 200
    assert got.json()["id"] == uid
    assert (await client.get(f"/identity/users/{uuid.uuid4()}")).status_code == 404


@pytest.mark.asyncio
async def test_patch_user_updates(client) -> None:
    created = await client.post("/identity/users", json=_payload())
    uid = created.json()["id"]
    patched = await client.patch(
        f"/identity/users/{uid}",
        json={"full_name": "Grace Hopper", "is_active": False},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["full_name"] == "Grace Hopper"
    assert body["is_active"] is False


@pytest.mark.asyncio
async def test_patch_missing_user_404(client) -> None:
    resp = await client.patch(
        f"/identity/users/{uuid.uuid4()}", json={"full_name": "X"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_devices_empty(client) -> None:
    created = await client.post("/identity/users", json=_payload())
    uid = created.json()["id"]
    resp = await client.get(f"/identity/devices/{uid}")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_devices_unknown_user_404(client) -> None:
    resp = await client.get(f"/identity/devices/{uuid.uuid4()}")
    assert resp.status_code == 404
