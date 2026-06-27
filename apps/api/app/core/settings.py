"""Application configuration for Project Rocky.

Operational policy (e.g. session and refresh-token lifetimes) lives here as
configuration, separate from capability behavior. Values are read from
environment variables with sensible defaults, so capabilities consume policy
rather than defining it.

Environment variables
---------------------
* ``SESSION_TTL_DAYS`` — session lifetime in days (default ``30``).
* ``REFRESH_TOKEN_TTL_DAYS`` — refresh-token lifetime in days (default ``30``).
"""
from __future__ import annotations

import os
from datetime import timedelta

_DEFAULT_SESSION_TTL_DAYS = 30
_DEFAULT_REFRESH_TOKEN_TTL_DAYS = 30


def _ttl_days(env_var: str, default_days: int) -> timedelta:
    raw = os.getenv(env_var)
    if not raw:
        return timedelta(days=default_days)
    try:
        days = int(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise RuntimeError(
            f"{env_var} must be an integer number of days, got {raw!r}"
        ) from exc
    return timedelta(days=days)


def get_session_ttl() -> timedelta:
    """Configured session lifetime."""
    return _ttl_days("SESSION_TTL_DAYS", _DEFAULT_SESSION_TTL_DAYS)


def get_refresh_token_ttl() -> timedelta:
    """Configured refresh-token lifetime."""
    return _ttl_days(
        "REFRESH_TOKEN_TTL_DAYS", _DEFAULT_REFRESH_TOKEN_TTL_DAYS
    )


# Convenience module-level constants, evaluated at import time. Functions
# above remain the source of truth for callers that need late binding.
SESSION_TTL: timedelta = get_session_ttl()
REFRESH_TOKEN_TTL: timedelta = get_refresh_token_ttl()


__all__ = [
    "SESSION_TTL",
    "REFRESH_TOKEN_TTL",
    "get_session_ttl",
    "get_refresh_token_ttl",
]
