from __future__ import annotations

import html
import re
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.dependencies import get_session
from app.db.base import Base
from app.main import app as fastapi_app
from app.models.auth_action_token import AuthActionToken
from app.models.session import Session
from app.models.user import User

import app.models  # noqa: F401

PASSWORD = "correct horse battery"
NEW_PASSWORD = "new correct horse battery"


@pytest.fixture(autouse=True)
def auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("REFRESH_TOKEN_PEPPER", "test-refresh-pepper")
    monkeypatch.setenv("EMAIL_VERIFICATION_COOLDOWN_SECONDS", "60")


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    fastapi_app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://testserver") as client:
            yield client, sessionmaker
    finally:
        fastapi_app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


def token_from_message(message) -> str:
    match = re.search(r'href="([^"]+)"', message.html)
    assert match
    return parse_qs(urlsplit(html.unescape(match.group(1))).query)["token"][0]


async def register(client: httpx.AsyncClient, email: str = "ada@example.com") -> httpx.Response:
    return await client.post(
        "/identity/users",
        json={"email": email, "password": PASSWORD, "full_name": "Ada Lovelace"},
    )


@pytest.mark.asyncio
async def test_registration_creates_unverified_user_and_sends_verification(ctx, fake_transactional_email) -> None:
    client, sessionmaker = ctx
    response = await register(client)
    assert response.status_code == 201
    assert response.json()["is_verified"] is False
    assert len(fake_transactional_email.messages) == 1
    message = fake_transactional_email.messages[0]
    assert message.recipient == "ada@example.com"
    assert message.reply_to == "support@rockyos.in"
    assert "Verify" in message.subject
    assert token_from_message(message)
    assert "http://localhost:5173/verify-email?" in message.html
    async with sessionmaker() as session:
        stored = (await session.execute(select(AuthActionToken))).scalar_one()
        assert stored.token_hash not in message.html
        assert stored.purpose == "verify_email"


@pytest.mark.asyncio
async def test_registration_surfaces_delivery_failure_without_losing_account(ctx, fake_transactional_email) -> None:
    client, sessionmaker = ctx
    fake_transactional_email.fail = True
    response = await register(client)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "VERIFICATION_DELIVERY_FAILED"
    async with sessionmaker() as session:
        user = (await session.execute(select(User).where(User.email == "ada@example.com"))).scalar_one()
        assert user.is_verified is False
        token = (await session.execute(select(AuthActionToken).where(AuthActionToken.user_id == user.id))).scalar_one()
        assert token.consumed_at is not None


@pytest.mark.asyncio
async def test_verification_valid_invalid_expired_and_reused(ctx, fake_transactional_email) -> None:
    client, sessionmaker = ctx
    created = await register(client)
    user_id = uuid.UUID(created.json()["id"])
    token = token_from_message(fake_transactional_email.messages[-1])
    invalid = await client.post("/auth/email-verification/confirm", json={"token": "x" * 43})
    assert invalid.status_code == 400
    confirmed = await client.post("/auth/email-verification/confirm", json={"token": token})
    assert confirmed.status_code == 200
    async with sessionmaker() as session:
        assert (await session.get(User, user_id)).is_verified is True
    reused = await client.post("/auth/email-verification/confirm", json={"token": token})
    assert reused.status_code == 400

    await register(client, "expired@example.com")
    expired_token = token_from_message(fake_transactional_email.messages[-1])
    async with sessionmaker() as session:
        await session.execute(
            update(AuthActionToken)
            .where(AuthActionToken.user_id != user_id)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        await session.commit()
    expired = await client.post("/auth/email-verification/confirm", json={"token": expired_token})
    assert expired.status_code == 400


@pytest.mark.asyncio
async def test_verification_resend_cooldown_then_invalidates_old_token(ctx, fake_transactional_email) -> None:
    client, sessionmaker = ctx
    created = await register(client)
    user_id = uuid.UUID(created.json()["id"])
    old_token = token_from_message(fake_transactional_email.messages[-1])
    first = await client.post("/auth/email-verification/request", json={"email": "ada@example.com"})
    assert first.status_code == 202
    assert len(fake_transactional_email.messages) == 1
    async with sessionmaker() as session:
        await session.execute(
            update(AuthActionToken)
            .where(AuthActionToken.user_id == user_id)
            .values(created_at=datetime.now(timezone.utc) - timedelta(minutes=2))
        )
        await session.commit()
    second = await client.post("/auth/email-verification/request", json={"email": "ada@example.com"})
    assert second.status_code == 202
    assert len(fake_transactional_email.messages) == 2
    assert (await client.post("/auth/email-verification/confirm", json={"token": old_token})).status_code == 400


@pytest.mark.asyncio
async def test_unverified_login_has_stable_code_then_verified_login_works(ctx, fake_transactional_email) -> None:
    client, _ = ctx
    await register(client)
    blocked = await client.post("/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "EMAIL_NOT_VERIFIED"
    token = token_from_message(fake_transactional_email.messages[-1])
    assert (await client.post("/auth/email-verification/confirm", json={"token": token})).status_code == 200
    assert (await client.post("/auth/login", json={"email": "ada@example.com", "password": PASSWORD})).status_code == 200


@pytest.mark.asyncio
async def test_password_reset_is_enumeration_safe_and_single_use(ctx, fake_transactional_email) -> None:
    client, sessionmaker = ctx
    created = await register(client)
    user_id = uuid.UUID(created.json()["id"])
    fake_transactional_email.messages.clear()
    existing = await client.post("/auth/password-reset/request", json={"email": "ada@example.com"})
    unknown = await client.post("/auth/password-reset/request", json={"email": "missing@example.com"})
    assert existing.status_code == unknown.status_code == 202
    assert existing.json() == unknown.json()
    assert len(fake_transactional_email.messages) == 1
    reset_token = token_from_message(fake_transactional_email.messages[0])
    async with sessionmaker() as session:
        user = await session.get(User, user_id)
        user.is_verified = True
        await session.commit()
    reset = await client.post(
        "/auth/password-reset/confirm",
        json={"token": reset_token, "new_password": NEW_PASSWORD},
    )
    assert reset.status_code == 200
    assert (await client.post("/auth/password-reset/confirm", json={"token": reset_token, "new_password": NEW_PASSWORD})).status_code == 400
    assert (await client.post("/auth/login", json={"email": "ada@example.com", "password": PASSWORD})).status_code == 401
    new_login = await client.post("/auth/login", json={"email": "ada@example.com", "password": NEW_PASSWORD})
    assert new_login.status_code == 200
    assert (await client.post("/auth/refresh", json={"refresh_token": new_login.json()["refresh_token"]})).status_code == 200


@pytest.mark.asyncio
async def test_password_reset_delivery_failure_keeps_generic_response(ctx, fake_transactional_email) -> None:
    client, _ = ctx
    await register(client)
    fake_transactional_email.fail = True
    response = await client.post("/auth/password-reset/request", json={"email": "ada@example.com"})
    assert response.status_code == 202
    unknown = await client.post("/auth/password-reset/request", json={"email": "unknown@example.com"})
    assert unknown.status_code == 202
    assert response.json() == unknown.json()


@pytest.mark.asyncio
async def test_password_reset_invalid_expired_and_revokes_sessions(ctx, fake_transactional_email) -> None:
    client, sessionmaker = ctx
    created = await register(client)
    user_id = uuid.UUID(created.json()["id"])
    async with sessionmaker() as session:
        user = await session.get(User, user_id)
        user.is_verified = True
        await session.commit()
    login = await client.post("/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    old_refresh = login.json()["refresh_token"]
    fake_transactional_email.messages.clear()
    await client.post("/auth/password-reset/request", json={"email": "ada@example.com"})
    expired_token = token_from_message(fake_transactional_email.messages[-1])
    async with sessionmaker() as session:
        await session.execute(update(AuthActionToken).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await session.commit()
    assert (await client.post("/auth/password-reset/confirm", json={"token": expired_token, "new_password": NEW_PASSWORD})).status_code == 400
    assert (await client.post("/auth/password-reset/confirm", json={"token": "z" * 43, "new_password": NEW_PASSWORD})).status_code == 400

    await client.post("/auth/password-reset/request", json={"email": "ada@example.com"})
    valid_token = token_from_message(fake_transactional_email.messages[-1])
    assert (await client.post("/auth/password-reset/confirm", json={"token": valid_token, "new_password": NEW_PASSWORD})).status_code == 200
    assert (await client.post("/auth/refresh", json={"refresh_token": old_refresh})).status_code == 401
    async with sessionmaker() as session:
        sessions = (await session.execute(select(Session).where(Session.user_id == user_id))).scalars().all()
        assert sessions and all(item.revoked_at is not None for item in sessions)
