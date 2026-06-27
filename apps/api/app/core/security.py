"""Authentication primitives for Project Rocky.

A framework-independent foundation providing:

* Password hashing and verification using Argon2id (via ``pwdlib``).
* JWT access/refresh token creation and decoding (via ``python-jose``).

This module deliberately contains **no** web-framework code (no FastAPI
routes, dependencies, or middleware). It is a pure capability that higher
layers compose. Configuration is read from environment variables.

Environment variables
----------------------
* ``SECRET_KEY`` — signing key for JWTs. **Required.**
* ``ALGORITHM`` — JWT signing algorithm (default ``"HS256"``).
* ``ACCESS_TOKEN_EXPIRE_MINUTES`` — access token lifetime (default ``30``).
* ``REFRESH_TOKEN_EXPIRE_DAYS`` — refresh token lifetime (default ``7``).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Literal

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

TokenType = Literal["access", "refresh"]

_ACCESS: Final[TokenType] = "access"
_REFRESH: Final[TokenType] = "refresh"

_DEFAULT_ALGORITHM: Final[str] = "HS256"
_DEFAULT_ACCESS_MINUTES: Final[int] = 30
_DEFAULT_REFRESH_DAYS: Final[int] = 7


# --------------------------------------------------------------------------- #
# Exceptions (framework-independent domain errors)
# --------------------------------------------------------------------------- #
class SecurityError(Exception):
    """Base class for security/auth errors."""


class TokenError(SecurityError):
    """Base class for token-related errors."""


class InvalidTokenError(TokenError):
    """Raised when a token is malformed, has a bad signature, or fails
    validation (including an unexpected token ``type``)."""


class ExpiredTokenError(TokenError):
    """Raised when a token's ``exp`` claim is in the past."""


# --------------------------------------------------------------------------- #
# Configuration helpers (read lazily so importing this module is side-effect
# free and does not require SECRET_KEY to be present at import time).
# --------------------------------------------------------------------------- #
def _get_secret_key() -> str:
    secret = os.getenv("SECRET_KEY")
    if not secret:
        raise SecurityError(
            "SECRET_KEY environment variable is not set."
        )
    return secret


def _get_algorithm() -> str:
    return os.getenv("ALGORITHM", _DEFAULT_ALGORITHM)


def _get_access_expire_minutes() -> int:
    raw = os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES")
    return int(raw) if raw else _DEFAULT_ACCESS_MINUTES


def _get_refresh_expire_days() -> int:
    raw = os.getenv("REFRESH_TOKEN_EXPIRE_DAYS")
    return int(raw) if raw else _DEFAULT_REFRESH_DAYS


# --------------------------------------------------------------------------- #
# Password hashing (Argon2id)
# --------------------------------------------------------------------------- #
_password_hash: Final[PasswordHash] = PasswordHash((Argon2Hasher(),))


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2id.

    Returns a self-describing PHC-format hash string (includes algorithm,
    parameters, and salt), suitable for direct storage.
    """
    return _password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify a plaintext password against an Argon2id hash.

    Returns ``False`` for a mismatch or an unparseable/invalid hash rather
    than raising, so callers can treat verification as a simple boolean.
    """
    try:
        return _password_hash.verify(password, hashed)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# JWT tokens
# --------------------------------------------------------------------------- #
def _create_token(
    subject: str | uuid.UUID,
    token_type: TokenType,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra_claims:
        # Never allow callers to silently override reserved claims.
        reserved = {"sub", "type", "iat", "exp"}
        payload.update(
            {k: v for k, v in extra_claims.items() if k not in reserved}
        )
    return jwt.encode(
        payload, _get_secret_key(), algorithm=_get_algorithm()
    )


def create_access_token(
    subject: str | uuid.UUID,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create a signed access token for ``subject`` (a user UUID).

    ``expires_delta`` overrides the configured default lifetime when given.
    """
    delta = expires_delta or timedelta(
        minutes=_get_access_expire_minutes()
    )
    return _create_token(subject, _ACCESS, delta, extra_claims)


def create_refresh_token(
    subject: str | uuid.UUID,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create a signed refresh token for ``subject`` (a user UUID).

    ``expires_delta`` overrides the configured default lifetime when given.
    """
    delta = expires_delta or timedelta(
        days=_get_refresh_expire_days()
    )
    return _create_token(subject, _REFRESH, delta, extra_claims)


def decode_token(
    token: str,
    expected_type: TokenType | None = None,
) -> dict[str, Any]:
    """Decode and validate a JWT, returning its claims.

    Signature and ``exp`` are always verified. If ``expected_type`` is
    provided, the token's ``type`` claim must match it.

    Raises
    ------
    ExpiredTokenError
        If the token has expired.
    InvalidTokenError
        If the token is malformed, has an invalid signature, or its
        ``type`` does not match ``expected_type``.
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token, _get_secret_key(), algorithms=[_get_algorithm()]
        )
    except ExpiredSignatureError as exc:
        raise ExpiredTokenError("Token has expired.") from exc
    except JWTError as exc:
        raise InvalidTokenError("Token is invalid.") from exc

    if expected_type is not None and claims.get("type") != expected_type:
        raise InvalidTokenError(
            f"Expected token of type {expected_type!r}, "
            f"got {claims.get('type')!r}."
        )
    return claims


__all__ = [
    "SecurityError",
    "TokenError",
    "InvalidTokenError",
    "ExpiredTokenError",
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
]
