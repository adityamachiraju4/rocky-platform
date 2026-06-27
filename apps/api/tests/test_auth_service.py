"""Unit tests for app.services.auth_service.

The database session is fully mocked — these tests do not touch a real
database. ``AsyncSession.execute`` is awaited and returns a result object
whose ``scalar_one_or_none`` is synchronous, so the mock mirrors that shape.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.security import (
    create_access_token,
    decode_token,
    hash_password,
)
from app.services.auth_service import (
    AuthService,
    InactiveUserError,
    InvalidCredentialsError,
    TokenPair,
    UnverifiedUserError,
)

SECRET = "test-secret-key"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    monkeypatch.setenv("REFRESH_TOKEN_EXPIRE_DAYS", "7")


def _make_session(returned_user) -> MagicMock:
    """Build a mock AsyncSession whose execute() returns the given user."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=returned_user)
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    return session


def _make_user(
    *,
    password: str = "correct-password",
    is_active: bool = True,
    is_verified: bool = True,
):
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "user@example.com"
    user.password_hash = hash_password(password)
    user.is_active = is_active
    user.is_verified = is_verified
    return user


# --------------------------------------------------------------------------- #
# Success path
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_authenticate_success_returns_token_pair() -> None:
    user = _make_user()
    service = AuthService(_make_session(user))

    tokens = await service.authenticate("user@example.com", "correct-password")

    assert isinstance(tokens, TokenPair)
    assert tokens.token_type == "bearer"
    assert tokens.access_token and tokens.refresh_token


@pytest.mark.asyncio
async def test_tokens_have_correct_subject_and_types() -> None:
    user = _make_user()
    service = AuthService(_make_session(user))

    tokens = await service.authenticate("user@example.com", "correct-password")

    access = decode_token(tokens.access_token, expected_type="access")
    refresh = decode_token(tokens.refresh_token, expected_type="refresh")
    assert access["sub"] == str(user.id)
    assert refresh["sub"] == str(user.id)
    assert access["type"] == "access"
    assert refresh["type"] == "refresh"


@pytest.mark.asyncio
async def test_password_hash_never_exposed() -> None:
    user = _make_user()
    service = AuthService(_make_session(user))

    tokens = await service.authenticate("user@example.com", "correct-password")

    # The result object carries no hash, and the hash is not embedded in
    # either token's claims.
    assert not hasattr(tokens, "password_hash")
    assert user.password_hash not in tokens.access_token
    assert user.password_hash not in tokens.refresh_token
    access = decode_token(tokens.access_token)
    assert "password_hash" not in access


# --------------------------------------------------------------------------- #
# Failure paths
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_unknown_email_raises_invalid_credentials() -> None:
    service = AuthService(_make_session(None))
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate("nobody@example.com", "whatever")


@pytest.mark.asyncio
async def test_wrong_password_raises_invalid_credentials() -> None:
    user = _make_user(password="correct-password")
    service = AuthService(_make_session(user))
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate("user@example.com", "wrong-password")


@pytest.mark.asyncio
async def test_inactive_user_raises_inactive_error() -> None:
    user = _make_user(is_active=False)
    service = AuthService(_make_session(user))
    with pytest.raises(InactiveUserError):
        await service.authenticate("user@example.com", "correct-password")


@pytest.mark.asyncio
async def test_unverified_user_raises_unverified_error() -> None:
    user = _make_user(is_active=True, is_verified=False)
    service = AuthService(_make_session(user))
    with pytest.raises(UnverifiedUserError):
        await service.authenticate("user@example.com", "correct-password")


@pytest.mark.asyncio
async def test_inactive_takes_precedence_over_unverified() -> None:
    # An inactive AND unverified user should surface InactiveUserError first.
    user = _make_user(is_active=False, is_verified=False)
    service = AuthService(_make_session(user))
    with pytest.raises(InactiveUserError):
        await service.authenticate("user@example.com", "correct-password")


@pytest.mark.asyncio
async def test_wrong_password_on_inactive_user_is_invalid_credentials() -> None:
    # Credentials are checked before account state: a wrong password on an
    # inactive account must report InvalidCredentials, not InactiveUser,
    # so account state never leaks for bad credentials.
    user = _make_user(password="correct-password", is_active=False)
    service = AuthService(_make_session(user))
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate("user@example.com", "wrong-password")


@pytest.mark.asyncio
async def test_session_execute_was_awaited() -> None:
    user = _make_user()
    session = _make_session(user)
    service = AuthService(session)
    await service.authenticate("user@example.com", "correct-password")
    session.execute.assert_awaited_once()
