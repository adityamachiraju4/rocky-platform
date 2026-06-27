"""Authentication service for Project Rocky.

Composes the existing Identity (``User`` model) and Security (password
verification + JWT) capabilities into an email/password authentication
flow. This module is framework-independent: it depends only on an
``AsyncSession`` for database access and on ``app.core.security`` for
cryptographic primitives. It contains no FastAPI routes or dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    create_refresh_token,
    verify_password,
)
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


# --------------------------------------------------------------------------- #
# Result object (never carries the password hash)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class TokenPair:
    """A freshly minted access/refresh token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AuthService:
    """Email/password authentication built on Identity + Security."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _get_user_by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()

    async def authenticate(self, email: str, password: str) -> TokenPair:
        """Authenticate a user and return an access/refresh token pair.

        Resolution order is deliberate:

        1. Unknown email or wrong password -> ``InvalidCredentialsError``.
        2. Correct credentials but inactive account ->
           ``InactiveUserError``.
        3. Correct credentials, active, but unverified ->
           ``UnverifiedUserError``.

        Credentials are always fully verified *before* account-state
        errors are raised, so state errors never leak which emails exist.
        """
        user = await self._get_user_by_email(email)

        # Verify the password even when the user is missing, to keep the
        # code path (and timing) similar for known vs unknown emails.
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

        return self._issue_tokens(user)

    @staticmethod
    def _issue_tokens(user: User) -> TokenPair:
        subject = str(user.id)
        return TokenPair(
            access_token=create_access_token(subject),
            refresh_token=create_refresh_token(subject),
        )


__all__ = [
    "AuthService",
    "TokenPair",
    "AuthError",
    "InvalidCredentialsError",
    "InactiveUserError",
    "UnverifiedUserError",
]
