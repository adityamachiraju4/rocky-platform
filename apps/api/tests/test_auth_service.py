"""Unit tests for app.services.auth_service.

AuthService now has a single responsibility: verify credentials + account
state and return the authenticated ``User``. Token/session issuance moved to
``app.auth.service.AuthLifecycleService`` (tested separately). The database
session is fully mocked — no real database.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.security import hash_password
from app.models.user import User
from app.services.auth_service import (
    AuthService,
    InactiveUserError,
    InvalidCredentialsError,
    UnverifiedUserError,
)

SECRET = "test-secret-key"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    # hash_password/verify_password need no secret, but keep parity with the
    # rest of the suite and guard against accidental token use.
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("ALGORITHM", "HS256")


def _make_session(returned_user) -> MagicMock:
    """Mock AsyncSession whose execute() returns the given user."""
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
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.email = "user@example.com"
    user.password_hash = hash_password(password)
    user.is_active = is_active
    user.is_verified = is_verified
    return user


# --------------------------------------------------------------------------- #
# Success path — returns the User, never a token or a hash-bearing object
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_authenticate_success_returns_user() -> None:
    user = _make_user()
    service = AuthService(_make_session(user))

    result = await service.authenticate(
        "user@example.com", "correct-password"
    )

    assert result is user


@pytest.mark.asyncio
async def test_authenticate_awaits_execute() -> None:
    user = _make_user()
    session = _make_session(user)
    service = AuthService(session)
    await service.authenticate("user@example.com", "correct-password")
    session.execute.assert_awaited_once()


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
    user = _make_user(is_active=False, is_verified=False)
    service = AuthService(_make_session(user))
    with pytest.raises(InactiveUserError):
        await service.authenticate("user@example.com", "correct-password")


@pytest.mark.asyncio
async def test_wrong_password_on_inactive_user_is_invalid_credentials() -> None:
    # Credentials are checked before account state: a wrong password on an
    # inactive account must report InvalidCredentials, not InactiveUser, so
    # account state never leaks for bad credentials.
    user = _make_user(password="correct-password", is_active=False)
    service = AuthService(_make_session(user))
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate("user@example.com", "wrong-password")
