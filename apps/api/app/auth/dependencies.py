"""Dependency-injection wiring for the Authentication capability.

Capability-local DI: the lifecycle service is composed here, consuming the
Platform ``get_session`` provider downward. Platform never imports this module
— dependency direction is Capability -> Platform.

``get_current_user`` enforces strict, authoritative session validation on every
authenticated request (D3): the access token must be a valid ``access`` JWT
carrying ``sub`` and ``sid``; the referenced user and session must exist, the
session must be active, and its ``session_version`` must still match the user's
authoritative counter. This makes logout and invalidation take immediate effect
on access tokens, at the cost of one session lookup per request.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session
from app.core.security import (
    ExpiredTokenError,
    InvalidTokenError,
    decode_token,
)
from app.models.user import User
from app.services.session_service import (
    SessionService,
    SessionVersionMismatchError,
)

from .exceptions import InvalidAccessTokenError
from .service import AuthLifecycleService


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthLifecycleService:
    return AuthLifecycleService(session)


AuthServiceDep = Annotated[
    AuthLifecycleService, Depends(get_auth_service)
]


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise InvalidAccessTokenError("Missing Authorization header.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise InvalidAccessTokenError("Malformed Authorization header.")
    return token


async def _resolve_current_user(
    session: AsyncSession,
    authorization: str | None,
) -> User:
    """Resolve and fully validate the current user from a bearer access token.

    Raises :class:`InvalidAccessTokenError` for any failure (missing/malformed
    header, invalid/expired token, wrong type, missing ``sid``, unknown user or
    session, inactive session, or session-version mismatch). The router
    translates this into ``401``.
    """
    token = _extract_bearer(authorization)
    try:
        claims = decode_token(token, expected_type="access")
    except (InvalidTokenError, ExpiredTokenError) as exc:
        raise InvalidAccessTokenError("Invalid or expired access token.") from exc

    sub = claims.get("sub")
    sid = claims.get("sid")
    if not sub or not sid:
        raise InvalidAccessTokenError("Access token missing sub/sid.")
    try:
        user_id = uuid.UUID(sub)
        session_id = uuid.UUID(sid)
    except (ValueError, TypeError) as exc:
        raise InvalidAccessTokenError("Access token sub/sid malformed.") from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise InvalidAccessTokenError("User not found or inactive.")

    sessions = SessionService(session)
    sess = await sessions.get_session(session_id)
    if sess is None or not SessionService.is_session_active(sess):
        raise InvalidAccessTokenError("Session not found or inactive.")
    if sess.user_id != user_id:
        raise InvalidAccessTokenError("Session does not belong to user.")
    try:
        await sessions.validate_session_version(sess)
    except SessionVersionMismatchError as exc:
        raise InvalidAccessTokenError("Session has been invalidated.") from exc

    return user


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """FastAPI dependency: resolve the current user or raise HTTP 401.

    Delegates to :func:`_resolve_current_user` and translates the domain
    :class:`InvalidAccessTokenError` into a ``401`` at the framework
    boundary (with a ``WWW-Authenticate: Bearer`` challenge).
    """
    try:
        return await _resolve_current_user(session, authorization)
    except InvalidAccessTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentUserDep = Annotated[User, Depends(get_current_user)]


__all__ = [
    "get_auth_service",
    "AuthServiceDep",
    "get_current_user",
    "CurrentUserDep",
]
