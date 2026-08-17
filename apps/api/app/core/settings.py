"""Application configuration for Project Rocky.

Operational policy (e.g. session and refresh-token lifetimes) lives here as
configuration, separate from capability behavior. Values are read from
environment variables with sensible defaults, so capabilities consume policy
rather than defining it.

Environment variables
---------------------
* ``SESSION_TTL_DAYS`` — session lifetime in days (default ``30``).
* ``REFRESH_TOKEN_TTL_DAYS`` — refresh-token lifetime in days (default ``30``).
* ``OPENAI_API_KEY`` — optional key for model-backed understanding.
* ``OPENAI_MODEL`` — optional OpenAI Responses model for understanding.
* ``OPENAI_TIMEOUT_SECONDS`` — optional provider timeout.
* ``OPENAI_TTS_API_KEY`` — optional official OpenAI key for speech rendering.
* ``OPENAI_TTS_BASE_URL`` — optional speech API base URL.
* ``OPENAI_TTS_MODEL`` — optional OpenAI speech model for spoken replies.
* ``OPENAI_TTS_VOICE`` — optional Rocky identity voice.
* ``OPENAI_TTS_SPEED`` — optional speech speed.
* ``LOCAL_TTS_ENABLED`` — enable/disable local neural speech.
* ``LOCAL_TTS_PROVIDER`` — local speech provider name.
* ``LOCAL_TTS_VOICE`` — local Rocky audition/default voice.
* ``LOCAL_TTS_SPEED`` — local speech speed.
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
_DEFAULT_OPENAI_MODEL = "gpt-5-nano"
_DEFAULT_OPENAI_TIMEOUT_SECONDS = 4.0
_DEFAULT_OPENAI_TTS_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
_DEFAULT_OPENAI_TTS_VOICE = "cedar"
_DEFAULT_OPENAI_TTS_SPEED = 0.95
_DEFAULT_LOCAL_TTS_ENABLED = True
_DEFAULT_LOCAL_TTS_PROVIDER = "kokoro"
_DEFAULT_LOCAL_TTS_VOICE = "am_adam"
_DEFAULT_LOCAL_TTS_SPEED = 0.95
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


def get_openai_api_key() -> str | None:
    raw = os.getenv("OPENAI_API_KEY")
    if not raw:
        return None
    return raw


def get_openai_model() -> str:
    return os.getenv("OPENAI_MODEL") or _DEFAULT_OPENAI_MODEL


def get_openai_timeout_seconds() -> float:
    raw = os.getenv("OPENAI_TIMEOUT_SECONDS")
    if not raw:
        return _DEFAULT_OPENAI_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise RuntimeError(
            f"OPENAI_TIMEOUT_SECONDS must be numeric, got {raw!r}"
        ) from exc


def get_openai_tts_api_key() -> str | None:
    raw = os.getenv("OPENAI_TTS_API_KEY")
    if raw:
        return raw
    if os.getenv("OPENAI_BASE_URL"):
        return None
    return get_openai_api_key()


def get_openai_tts_base_url() -> str:
    return os.getenv("OPENAI_TTS_BASE_URL") or _DEFAULT_OPENAI_TTS_BASE_URL


def get_openai_tts_model() -> str:
    raw = os.getenv("OPENAI_TTS_MODEL")
    if not raw or raw == "gpt-4o-mini-tts-2025-12-15":
        return _DEFAULT_OPENAI_TTS_MODEL
    return raw


def get_openai_tts_voice() -> str:
    return os.getenv("OPENAI_TTS_VOICE") or _DEFAULT_OPENAI_TTS_VOICE


def get_openai_tts_speed() -> float:
    raw = os.getenv("OPENAI_TTS_SPEED")
    if not raw:
        return _DEFAULT_OPENAI_TTS_SPEED
    try:
        return float(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise RuntimeError(
            f"OPENAI_TTS_SPEED must be numeric, got {raw!r}"
        ) from exc


def get_local_tts_enabled() -> bool:
    raw = os.getenv("LOCAL_TTS_ENABLED")
    if raw is None or raw == "":
        return _DEFAULT_LOCAL_TTS_ENABLED
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_local_tts_provider() -> str:
    return os.getenv("LOCAL_TTS_PROVIDER") or _DEFAULT_LOCAL_TTS_PROVIDER


def get_local_tts_voice() -> str:
    return os.getenv("LOCAL_TTS_VOICE") or _DEFAULT_LOCAL_TTS_VOICE


def get_local_tts_speed() -> float:
    raw = os.getenv("LOCAL_TTS_SPEED")
    if not raw:
        return _DEFAULT_LOCAL_TTS_SPEED
    try:
        return float(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise RuntimeError(
            f"LOCAL_TTS_SPEED must be numeric, got {raw!r}"
        ) from exc


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
    "get_openai_api_key",
    "get_openai_model",
    "get_openai_timeout_seconds",
    "get_openai_tts_api_key",
    "get_openai_tts_base_url",
    "get_openai_tts_model",
    "get_openai_tts_voice",
    "get_openai_tts_speed",
    "get_local_tts_enabled",
    "get_local_tts_provider",
    "get_local_tts_voice",
    "get_local_tts_speed",
]
