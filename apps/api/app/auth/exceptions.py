"""Domain exceptions for the Authentication lifecycle.

Pure domain errors — no HTTP semantics. The router translates them into HTTP
responses. Credential-verification errors (InvalidCredentialsError, etc.) live
in ``app.services.auth_service``; session errors live in
``app.services.session_service``. The errors here cover the refresh/logout and
current-user paths that the lifecycle orchestrator owns.
"""
from __future__ import annotations


class AuthLifecycleError(Exception):
    """Base class for authentication-lifecycle errors."""


class InvalidRefreshTokenError(AuthLifecycleError):
    """Raised when a presented refresh token is unknown, already rotated,
    revoked, or expired. A single error type avoids leaking which of these
    conditions held."""


class InvalidAccessTokenError(AuthLifecycleError):
    """Raised when an access token is missing, malformed, of the wrong type,
    or references a user/session that no longer validates."""


__all__ = [
    "AuthLifecycleError",
    "InvalidRefreshTokenError",
    "InvalidAccessTokenError",
]
