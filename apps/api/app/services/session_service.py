"""Session service for Rocky (Capability 002, Phase 1).

Owns all business logic for persistent sessions and their refresh tokens.
Refresh tokens are generated as high-entropy random secrets; only their
SHA-256 hashes are ever persisted. The plaintext is returned to the caller
exactly once at creation time and never stored.

Framework-independent: database access is only via an injected
``AsyncSession``. No FastAPI.

Out of scope for Phase 1 (see ADR-0006): refresh endpoint, JWT rotation,
automatic cleanup jobs, device fingerprinting, multi-device logout.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_refresh_token_ttl, get_session_ttl
from app.models.refresh_token import RefreshToken
from app.models.session import Session

_REFRESH_TOKEN_BYTES = 32  # 256 bits of entropy


# --------------------------------------------------------------------------- #
# Typed errors
# --------------------------------------------------------------------------- #
class SessionError(Exception):
    """Base class for session errors."""


class SessionNotFoundError(SessionError):
    """Raised when a session cannot be located."""


# --------------------------------------------------------------------------- #
# Result object — carries the plaintext refresh token exactly once, and
# never the stored hash.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class CreatedSession:
    """Returned by :meth:`SessionService.create_session`.

    ``refresh_token`` is the plaintext secret; it is shown only here and is
    not recoverable later. The persisted hash is never exposed.
    """

    session: Session
    refresh_token: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(plaintext: str) -> str:
    """Hash a refresh token for storage (SHA-256 hex digest).

    A refresh token is a high-entropy random value, so a fast cryptographic
    hash is appropriate and lets us index/look it up by hash.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


class SessionService:
    """Business logic for sessions and their refresh tokens."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    # ----------------------------------------------------------------- #
    # Create
    # ----------------------------------------------------------------- #
    async def create_session(
        self,
        *,
        user_id: uuid.UUID,
        device_id: uuid.UUID,
        is_trusted: bool = False,
        session_ttl: timedelta | None = None,
        refresh_ttl: timedelta | None = None,
    ) -> CreatedSession:
        """Create a session plus its initial refresh token.

        Returns the persisted :class:`Session` and the **plaintext** refresh
        token (returned once; only the hash is stored).
        """
        now = _now()
        session = Session(
            user_id=user_id,
            device_id=device_id,
            created_at=now,
            last_activity=now,
            expires_at=now + (session_ttl or get_session_ttl()),
            is_trusted=is_trusted,
            session_version=1,
        )
        self._db.add(session)
        await self._db.flush()  # populate session.id

        plaintext = secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)
        refresh = RefreshToken(
            session_id=session.id,
            token_hash=_hash_token(plaintext),
            created_at=now,
            expires_at=now + (refresh_ttl or get_refresh_token_ttl()),
        )
        self._db.add(refresh)
        await self._db.commit()
        await self._db.refresh(session)

        return CreatedSession(session=session, refresh_token=plaintext)

    # ----------------------------------------------------------------- #
    # Read
    # ----------------------------------------------------------------- #
    async def get_session(
        self, session_id: uuid.UUID
    ) -> Session | None:
        """Return a session by id, or ``None`` if not found."""
        result = await self._db.execute(
            select(Session).where(Session.id == session_id)
        )
        return result.scalar_one_or_none()

    # ----------------------------------------------------------------- #
    # Revoke
    # ----------------------------------------------------------------- #
    async def revoke_session(self, session_id: uuid.UUID) -> Session:
        """Mark a session as revoked.

        Idempotent: revoking an already-revoked session leaves the original
        ``revoked_at`` untouched.

        Raises :class:`SessionNotFoundError` if the session does not exist.
        """
        session = await self.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(str(session_id))
        if session.revoked_at is None:
            session.revoked_at = _now()
            await self._db.commit()
            await self._db.refresh(session)
        return session

    # ----------------------------------------------------------------- #
    # Update activity
    # ----------------------------------------------------------------- #
    async def update_last_activity(
        self, session_id: uuid.UUID
    ) -> Session:
        """Bump ``last_activity`` to now.

        Raises :class:`SessionNotFoundError` if the session does not exist.
        """
        session = await self.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(str(session_id))
        session.last_activity = _now()
        await self._db.commit()
        await self._db.refresh(session)
        return session

    # ----------------------------------------------------------------- #
    # Active check
    # ----------------------------------------------------------------- #
    @staticmethod
    def is_session_active(session: Session) -> bool:
        """A session is active iff it is not revoked and not expired."""
        if session.revoked_at is not None:
            return False
        expires_at = session.expires_at
        # Treat naive datetimes as UTC for a safe comparison.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > _now()


__all__ = [
    "SessionService",
    "CreatedSession",
    "SessionError",
    "SessionNotFoundError",
]
