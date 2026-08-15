"""Authentication lifecycle API tests (login / refresh / logout / current-user).

Hermetic: in-memory SQLite (aiosqlite), schema from Base.metadata, Platform
get_session overridden. Mirrors tests/test_identity.py. Exercises the full
capability end-to-end through the mounted /auth router, plus get_current_user
via a temporary protected route.
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

from app.auth.dependencies import CurrentUserDep
from app.core.dependencies import get_session
from app.db.base import Base
from app.main import app as fastapi_app
from app.models.user import User

import app.models  # noqa: F401  (populate Base.metadata before create_all)

SECRET = "test-secret-key"
PEPPER = "test-refresh-pepper"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    monkeypatch.setenv("REFRESH_TOKEN_PEPPER", PEPPER)


# A temporary protected route so we can exercise get_current_user over HTTP.
@fastapi_app.get("/_test/whoami")
async def _whoami(user: CurrentUserDep) -> dict:
    return {"id": str(user.id), "email": user.email}


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
    password: str = "correct horse battery",
) -> str:
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/identity/users",
        json={"email": email, "password": password, "full_name": "Ada"},
    )
    assert resp.status_code == 201, resp.text
    uid = resp.json()["id"]
    # No verify endpoint exists; flip is_verified/is_active directly.
    async with sessionmaker() as s:
        await s.execute(
            update(User)
            .where(User.id == uuid.UUID(uid))
            .values(is_verified=True, is_active=True)
        )
        await s.commit()
    return email


def _login_body(email, password="correct horse battery", **extra):
    return {"email": email, "password": password, **extra}


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_login_success_returns_tokens(ctx) -> None:
    client, sm = ctx
    email = await _make_login_ready_user(client, sm)
    resp = await client.post("/auth/login", json=_login_body(email))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password_401(ctx) -> None:
    client, sm = ctx
    email = await _make_login_ready_user(client, sm)
    resp = await client.post(
        "/auth/login", json=_login_body(email, password="nope wrong pass")
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unverified_403(ctx) -> None:
    client, sm = ctx
    # Create a user but do NOT verify.
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    r = await client.post(
        "/identity/users",
        json={"email": email, "password": "correct horse battery"},
    )
    assert r.status_code == 201
    resp = await client.post("/auth/login", json=_login_body(email))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_login_reuses_device_by_client_id(ctx) -> None:
    client, sm = ctx
    email = await _make_login_ready_user(client, sm)
    cid = str(uuid.uuid4())
    r1 = await client.post(
        "/auth/login", json=_login_body(email, client_id=cid)
    )
    r2 = await client.post(
        "/auth/login", json=_login_body(email, client_id=cid)
    )
    assert r1.status_code == 200 and r2.status_code == 200
    # Find the user's id, then assert exactly one device exists for it.
    from app.models.device import Device
    async with sm() as s:
        u = (
            await s.execute(
                User.__table__.select().where(User.email == email)
            )
        ).first()
        uid = u.id
        devices = (
            await s.execute(
                Device.__table__.select().where(Device.user_id == uid)
            )
        ).all()
    assert len(devices) == 1  # reused, not duplicated


@pytest.mark.asyncio
async def test_login_without_client_id_creates_device_each_time(ctx) -> None:
    client, sm = ctx
    email = await _make_login_ready_user(client, sm)
    await client.post("/auth/login", json=_login_body(email))
    await client.post("/auth/login", json=_login_body(email))
    from app.models.device import Device
    async with sm() as s:
        u = (
            await s.execute(
                User.__table__.select().where(User.email == email)
            )
        ).first()
        devices = (
            await s.execute(
                Device.__table__.select().where(Device.user_id == u.id)
            )
        ).all()
    assert len(devices) == 2  # no client_id -> new device each login


# --------------------------------------------------------------------------- #
# Refresh (rotation)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_refresh_rotates_and_old_token_rejected(ctx) -> None:
    client, sm = ctx
    email = await _make_login_ready_user(client, sm)
    login = (await client.post("/auth/login", json=_login_body(email))).json()
    old_refresh = login["refresh_token"]

    r = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 200, r.text
    new_refresh = r.json()["refresh_token"]
    assert new_refresh and new_refresh != old_refresh

    # New token works...
    again = await client.post(
        "/auth/refresh", json={"refresh_token": new_refresh}
    )
    assert again.status_code == 200
    # ...and the rotated (old) token is now rejected.
    reused = await client.post(
        "/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert reused.status_code == 401


@pytest.mark.asyncio
async def test_refresh_unknown_token_401(ctx) -> None:
    client, sm = ctx
    resp = await client.post(
        "/auth/refresh", json={"refresh_token": "not-a-real-token"}
    )
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Logout
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_logout_revokes_then_refresh_rejected(ctx) -> None:
    client, sm = ctx
    email = await _make_login_ready_user(client, sm)
    login = (await client.post("/auth/login", json=_login_body(email))).json()
    refresh = login["refresh_token"]

    out = await client.post("/auth/logout", json={"refresh_token": refresh})
    assert out.status_code == 204
    # Session revoked -> refresh with same token now rejected.
    after = await client.post(
        "/auth/refresh", json={"refresh_token": refresh}
    )
    assert after.status_code == 401


@pytest.mark.asyncio
async def test_logout_unknown_token_is_idempotent_204(ctx) -> None:
    client, sm = ctx
    out = await client.post(
        "/auth/logout", json={"refresh_token": "unknown"}
    )
    assert out.status_code == 204


# --------------------------------------------------------------------------- #
# get_current_user
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_whoami_with_valid_access_token(ctx) -> None:
    client, sm = ctx
    email = await _make_login_ready_user(client, sm)
    access = (
        await client.post("/auth/login", json=_login_body(email))
    ).json()["access_token"]
    resp = await client.get(
        "/_test/whoami", headers={"Authorization": f"Bearer {access}"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == email


@pytest.mark.asyncio
async def test_whoami_without_token_401(ctx) -> None:
    client, sm = ctx
    resp = await client.get("/_test/whoami")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_whoami_rejects_refresh_token_as_access(ctx) -> None:
    client, sm = ctx
    email = await _make_login_ready_user(client, sm)
    refresh = (
        await client.post("/auth/login", json=_login_body(email))
    ).json()["refresh_token"]
    # Opaque refresh token is not a JWT access token.
    resp = await client.get(
        "/_test/whoami", headers={"Authorization": f"Bearer {refresh}"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_whoami_after_logout_401(ctx) -> None:
    client, sm = ctx
    email = await _make_login_ready_user(client, sm)
    login = (await client.post("/auth/login", json=_login_body(email))).json()
    access, refresh = login["access_token"], login["refresh_token"]
    # Valid before logout.
    assert (
        await client.get(
            "/_test/whoami", headers={"Authorization": f"Bearer {access}"}
        )
    ).status_code == 200
    await client.post("/auth/logout", json={"refresh_token": refresh})
    # Session now revoked -> strict get_current_user rejects the access token.
    after = await client.get(
        "/_test/whoami", headers={"Authorization": f"Bearer {access}"}
    )
    assert after.status_code == 401
