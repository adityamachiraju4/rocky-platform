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

# --- .env loading (Platform-003) ---
# Load the .env file exactly once, here in the centralized configuration
# module. Every path that needs config imports app.core.settings (runtime
# directly; Alembic transitively via app.db.session), so this is the single
# load site. The path is resolved relative to this module — settings.py lives
# at apps/api/app/core/settings.py, so the project .env is three parents up at
# apps/api/.env — which makes loading independent of the process working
# directory (pytest, alembic, and `uvicorn` may each start from a different
# CWD). Real environment variables take precedence over .env values
# (override=False), preserving existing behavior in CI and production.
from pathlib import Path as _Path

from dotenv import load_dotenv as _load_dotenv

_ENV_PATH = _Path(__file__).resolve().parents[2] / ".env"
_load_dotenv(_ENV_PATH, override=False)
# --- end .env loading ---

import os
from datetime import timedelta

class MissingConfigurationError(RuntimeError):
    """Raised when a required configuration value is missing."""

_DEFAULT_SESSION_TTL_DAYS = 30
_DEFAULT_REFRESH_TOKEN_TTL_DAYS = 30
_MISSING = object()


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

def get_refresh_token_pepper() -> bytes:
    """Return the server-side pepper used for refresh-token hashing."""

    raw = os.getenv("REFRESH_TOKEN_PEPPER", _MISSING)

    if raw is _MISSING or raw == "":
        raise MissingConfigurationError(
            "REFRESH_TOKEN_PEPPER is not configured."
        )

    return raw.encode("utf-8")


# Convenience module-level constants, evaluated at import time. Functions
# above remain the source of truth for callers that need late binding.
SESSION_TTL: timedelta = get_session_ttl()
REFRESH_TOKEN_TTL: timedelta = get_refresh_token_ttl()


__all__ = [
    "SESSION_TTL",
    "REFRESH_TOKEN_TTL",
    "MissingConfigurationError",
    "get_session_ttl",
    "get_refresh_token_ttl",
    "get_refresh_token_pepper",
]
