"""Projects capability API tests (create / list / get / update / isolation).

Hermetic: in-memory SQLite (aiosqlite), schema from Base.metadata, Platform
get_session overridden. Mirrors tests/test_auth_lifecycle.py.

The load-bearing assertion is cross-user isolation: ownership derives from
CurrentUserDep, never from client input, and another user's project is
indistinguishable from a nonexistent one (404, not 403) so ownership cannot
be probed.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.dependencies import get_session
from app.db.base import Base
from app.main import app as fastapi_app
from app.models.user import User

import app.models  # noqa: F401  (populate Base.metadata before create_all)

SECRET = "test-secret-key"
PEPPER = "test-refresh-pepper"
PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    monkeypatch.setenv("REFRESH_TOKEN_PEPPER", PEPPER)


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker]]:
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
            yield ac, sessionmaker
    finally:
        fastapi_app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


async def _make_login_ready_user(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker,
    *,
    email: str | None = None,
) -> str:
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/identity/users",
        json={"email": email, "password": PASSWORD, "full_name": "Ada"},
    )
    assert resp.status_code == 201, resp.text
    uid = resp.json()["id"]
    async with sessionmaker() as s:
        await s.execute(
            update(User)
            .where(User.id == uuid.UUID(uid))
            .values(is_verified=True, is_active=True)
        )
        await s.commit()
    return email


async def _auth_headers(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker,
) -> tuple[dict[str, str], str]:
    """Register + verify + login. Returns (headers, user_id)."""
    email = await _make_login_ready_user(client, sessionmaker)
    resp = await client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    access = resp.json()["access_token"]
    async with sessionmaker() as s:
        row = (
            await s.execute(
                User.__table__.select().where(User.email == email)
            )
        ).first()
    return {"Authorization": f"Bearer {access}"}, str(row.id)


# --------------------------------------------------------------------------- #
# Authentication is required
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_create_project_requires_auth(ctx) -> None:
    client, sm = ctx
    resp = await client.post("/projects", json={"name": "Nope"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_projects_requires_auth(ctx) -> None:
    client, sm = ctx
    resp = await client.get("/projects")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_create_project_assigns_owner_from_token(ctx) -> None:
    client, sm = ctx
    headers, uid = await _auth_headers(client, sm)
    resp = await client.post(
        "/projects",
        json={"name": "Rocky", "description": "personal intelligence"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Rocky"
    assert body["description"] == "personal intelligence"
    assert body["status"] == "active"
    assert body["user_id"] == uid  # ownership from CurrentUserDep


@pytest.mark.asyncio
async def test_create_project_ignores_client_supplied_user_id(ctx) -> None:
    """A forged user_id in the body must not change ownership."""
    client, sm = ctx
    headers, uid = await _auth_headers(client, sm)
    other = str(uuid.uuid4())
    resp = await client.post(
        "/projects",
        json={"name": "Forged", "user_id": other},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user_id"] == uid
    assert resp.json()["user_id"] != other


@pytest.mark.asyncio
async def test_create_project_rejects_empty_name(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    resp = await client.post("/projects", json={"name": ""}, headers=headers)
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# List / get / update
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_list_returns_only_my_projects(ctx) -> None:
    client, sm = ctx
    alice, _ = await _auth_headers(client, sm)
    bob, _ = await _auth_headers(client, sm)

    await client.post("/projects", json={"name": "A1"}, headers=alice)
    await client.post("/projects", json={"name": "A2"}, headers=alice)
    await client.post("/projects", json={"name": "B1"}, headers=bob)

    a = await client.get("/projects", headers=alice)
    b = await client.get("/projects", headers=bob)
    assert a.status_code == 200 and b.status_code == 200
    assert sorted(p["name"] for p in a.json()) == ["A1", "A2"]
    assert [p["name"] for p in b.json()] == ["B1"]


@pytest.mark.asyncio
async def test_get_own_project(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = (
        await client.post("/projects", json={"name": "Mine"}, headers=headers)
    ).json()["id"]
    resp = await client.get(f"/projects/{pid}", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Mine"


@pytest.mark.asyncio
async def test_update_own_project(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    pid = (
        await client.post("/projects", json={"name": "Old"}, headers=headers)
    ).json()["id"]
    resp = await client.patch(
        f"/projects/{pid}",
        json={"name": "New", "status": "archived"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "New"
    assert resp.json()["status"] == "archived"


@pytest.mark.asyncio
async def test_get_unknown_project_404(ctx) -> None:
    client, sm = ctx
    headers, _ = await _auth_headers(client, sm)
    resp = await client.get(f"/projects/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Cross-user isolation (the point of the sprint)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_other_user_cannot_get_my_project(ctx) -> None:
    client, sm = ctx
    alice, _ = await _auth_headers(client, sm)
    bob, _ = await _auth_headers(client, sm)
    pid = (
        await client.post("/projects", json={"name": "Secret"}, headers=alice)
    ).json()["id"]

    mine = await client.get(f"/projects/{pid}", headers=alice)
    theirs = await client.get(f"/projects/{pid}", headers=bob)
    assert mine.status_code == 200
    # 404, not 403: existence must not be probeable.
    assert theirs.status_code == 404


@pytest.mark.asyncio
async def test_other_user_cannot_update_my_project(ctx) -> None:
    client, sm = ctx
    alice, _ = await _auth_headers(client, sm)
    bob, _ = await _auth_headers(client, sm)
    pid = (
        await client.post("/projects", json={"name": "Secret"}, headers=alice)
    ).json()["id"]

    resp = await client.patch(
        f"/projects/{pid}", json={"name": "Hijacked"}, headers=bob
    )
    assert resp.status_code == 404
    # And the project is untouched.
    after = await client.get(f"/projects/{pid}", headers=alice)
    assert after.json()["name"] == "Secret"
