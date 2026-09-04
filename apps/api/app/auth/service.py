"""Authentication lifecycle orchestration for Project Rocky.

Owns login, refresh-token rotation, and logout. Composes:

* ``AuthService``      — credential verification (returns a ``User``).
* ``SessionService``   — session + refresh-token creation, revocation,
                          activity, and session-version validation.
* Identity repositories — device resolution/creation.

Transaction ownership: this orchestrator owns the unit of work. On login it
flushes a newly created device (no commit) so it has an id, then delegates to
``SessionService.create_session`` whose single commit persists device, session,
and refresh token atomically (all share one ``AsyncSession``). Rotation and
logout own their own commits here.

Framework-independent: no FastAPI. Depends only on an injected ``AsyncSession``.
"""
from __future__ import annotations

import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.core.settings import get_refresh_token_ttl
from app.identity.repository import DeviceRepository
from app.models.device import Device
from app.models.refresh_token import RefreshToken
from app.models.session import Session
from app.services.auth_service import AuthService
from app.services.session_service import (
    _REFRESH_TOKEN_BYTES,
    SessionService,
    _hash_token,
    _now,
)

from .exceptions import InvalidRefreshTokenError
from .schemas import LoginRequest, TokenResponse


class AuthLifecycleService:
    """Composes credential verification with session/refresh-token lifecycle."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session
        self._auth = AuthService(session)
        self._sessions = SessionService(session)
        self._devices = DeviceRepository(session)

    # ------------------------------------------------------------------ #
    # Access-token minting (single site; injects the session id claim)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _mint_access(user_id: uuid.UUID, session_id: uuid.UUID) -> str:
        return create_access_token(
            str(user_id), extra_claims={"sid": str(session_id)}
        )

    # ------------------------------------------------------------------ #
    # Device resolution (D2: client_id optional; never manufactured)
    # ------------------------------------------------------------------ #
    async def _resolve_device(
        self, user_id: uuid.UUID, payload: LoginRequest
    ) -> Device:
        if payload.client_id is not None:
            existing = await self._devices.get_by_user_and_client_id(
                user_id, payload.client_id
            )
            if existing is not None:
                existing.last_seen = _now()
                return existing
        device = Device(
            user_id=user_id,
            client_id=payload.client_id,
            device_name=payload.device_name,
            device_type=payload.device_type,
            platform=payload.platform,
            last_seen=_now(),
        )
        await self._devices.add(device)  # add + flush (no commit)
        return device

    # ------------------------------------------------------------------ #
    # Login
    # ------------------------------------------------------------------ #
    async def login(self, payload: LoginRequest) -> TokenResponse:
        user = await self._auth.authenticate(payload.email, payload.password)
        device = await self._resolve_device(user.id, payload)
        created = await self._sessions.create_session(
            user_id=user.id,
            device_id=device.id,
            session_version=user.session_version,
        )  # commits device + session + refresh atomically
        access = self._mint_access(user.id, created.session.id)
        return TokenResponse(
            access_token=access, refresh_token=created.refresh_token
        )

    # ------------------------------------------------------------------ #
    # Refresh (rotation with parent lineage)
    # ------------------------------------------------------------------ #
    async def _get_refresh_by_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self._db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def refresh(self, plaintext: str) -> TokenResponse:
        token = await self._get_refresh_by_hash(_hash_token(plaintext))
        if token is None or token.revoked_at is not None:
            raise InvalidRefreshTokenError("Refresh token is not valid.")
        if token.expires_at.tzinfo is None:
            expired = token.expires_at.replace(tzinfo=_now().tzinfo)
        else:
            expired = token.expires_at
        if expired <= _now():
            raise InvalidRefreshTokenError("Refresh token has expired.")

        session = await self._sessions.get_session(token.session_id)
        if session is None or not SessionService.is_session_active(session):
            raise InvalidRefreshTokenError("Session is not active.")
        # Authoritative invalidation check.
        await self._sessions.validate_session_version(session)

        # Rotate: revoke the presented token, mint a child.
        now = _now()
        token.revoked_at = now
        new_plaintext, new_row = self._sessions_mint_child(session, token, now)
        self._db.add(new_row)
        await self._db.commit()

        access = self._mint_access(session.user_id, session.id)
        return TokenResponse(
            access_token=access, refresh_token=new_plaintext
        )

    def _sessions_mint_child(
        self, session: Session, parent: RefreshToken, now
    ) -> tuple[str, RefreshToken]:
        """Create (but do not commit) a child refresh token for rotation.

        Uses the session service's configured refresh TTL and the canonical
        peppered hash so there is exactly one hashing implementation.
        """
        plaintext = secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)
        row = RefreshToken(
            session_id=session.id,
            token_hash=_hash_token(plaintext),
            created_at=now,
            expires_at=now + get_refresh_token_ttl(),
            parent_token_id=parent.id,
        )
        return plaintext, row

    # ------------------------------------------------------------------ #
    # Logout (revoke session + presented token lineage)
    # ------------------------------------------------------------------ #
    async def logout(self, plaintext: str) -> None:
        token = await self._get_refresh_by_hash(_hash_token(plaintext))
        if token is None:
            # Idempotent: unknown token is a no-op success.
            return
        now = _now()
        if token.revoked_at is None:
            token.revoked_at = now
        # Revoke the owning session (idempotent inside the service).
        await self._sessions.revoke_session(token.session_id)


__all__ = ["AuthLifecycleService"]
