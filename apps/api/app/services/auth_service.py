"""Authentication (credential verification) service for Project Rocky.

This service has a single responsibility: verify an email/password pair and
the account's state, returning the authenticated :class:`User`. It does NOT
mint tokens or create sessions — token/session/refresh lifecycle is owned by
``app.auth.service.AuthLifecycleService``, which composes this service with
``SessionService``.

Framework-independent: depends only on an ``AsyncSession`` and on
``app.core.security`` for password verification. No FastAPI.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.models.user import User


# --------------------------------------------------------------------------- #
# Typed domain exceptions
# --------------------------------------------------------------------------- #
class AuthError(Exception):
    """Base class for authentication errors."""


class InvalidCredentialsError(AuthError):
    """Raised when the email is unknown or the password does not match.

    The same error is used for both cases on purpose, so callers cannot
    distinguish "no such user" from "wrong password" (avoids user
    enumeration).
    """


class InactiveUserError(AuthError):
    """Raised when the user exists and authenticates but is not active."""


class UnverifiedUserError(AuthError):
    """Raised when the user exists and authenticates but is not verified."""


class AuthService:
    """Email/password credential verification built on Identity + Security."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _get_user_by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()

    async def authenticate(self, email: str, password: str) -> User:
        """Verify credentials and account state, returning the ``User``.

        Resolution order is deliberate:

        1. Unknown email or wrong password -> ``InvalidCredentialsError``.
        2. Correct credentials but inactive account -> ``InactiveUserError``.
        3. Correct credentials, active, but unverified ->
           ``UnverifiedUserError``.

        Credentials are always fully verified *before* account-state errors
        are raised, so state errors never leak which emails exist. Token and
        session issuance are the caller's responsibility.
        """
        user = await self._get_user_by_email(email)

        # Verify the password even when the user is missing, to keep the code
        # path (and timing) similar for known vs unknown emails.
        password_ok = (
            verify_password(password, user.password_hash)
            if user is not None
            else False
        )
        if user is None or not password_ok:
            raise InvalidCredentialsError("Invalid email or password.")

        if not user.is_active:
            raise InactiveUserError("User account is inactive.")
        if not user.is_verified:
            raise UnverifiedUserError("User account is not verified.")

        return user


__all__ = [
    "AuthService",
    "AuthError",
    "InvalidCredentialsError",
    "InactiveUserError",
    "UnverifiedUserError",
]
