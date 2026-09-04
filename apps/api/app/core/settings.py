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
* ``TRANSCRIPTION_PROVIDER`` — transcription provider name.
* ``LOCAL_WHISPER_MODEL`` — local Whisper model size/name.
* ``LOCAL_WHISPER_DEVICE`` — local Whisper execution device.
* ``LOCAL_WHISPER_COMPUTE_TYPE`` — local Whisper compute type.
* ``LOCAL_WHISPER_LANGUAGE`` — local Whisper transcription language.
* ``LOCAL_WHISPER_WARMUP`` — warm local Whisper after API startup.
* ``OPENAI_TRANSCRIPTION_API_KEY`` — optional official OpenAI key for STT.
* ``OPENAI_TRANSCRIPTION_BASE_URL`` — optional transcription API base URL.
* ``OPENAI_TRANSCRIPTION_MODEL`` — optional OpenAI transcription model.
* ``LIVE_TIMEOUT_SECONDS`` — optional timeout for live-information providers.
* ``NEWS_API_KEY`` — optional NewsAPI key for current news.
* ``FINNHUB_API_KEY`` — optional Finnhub key for market quotes.
* ``TAVILY_API_KEY`` — optional Tavily key for current web search.
* ``GEOAPIFY_API_KEY`` — optional Geoapify key for places search.
* ``THESPORTSDB_API_KEY`` — optional TheSportsDB key for sports lookup.
* ``LOCAL_TTS_ENABLED`` — enable/disable local neural speech.
* ``LOCAL_TTS_PROVIDER`` — local speech provider name.
* ``LOCAL_TTS_VOICE`` — local Rocky audition/default voice.
* ``LOCAL_TTS_SPEED`` — local speech speed.
* ``LOCAL_TTS_WARMUP`` — warm local speech after API startup.
* ``CORS_ALLOWED_ORIGINS`` — comma-separated browser/native app origins allowed
  to call private APIs cross-origin.
* ``PUBLIC_APP_URL`` — public web-app origin used for auth action links.
* ``EMAIL_FROM`` — verified transactional sender identity.
* ``RESEND_API_KEY`` — Resend API credential for transactional email.
* ``AUTH_ACTION_TOKEN_PEPPER`` — secret used to hash verification/reset tokens.
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
from urllib.parse import urlsplit

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
_DEFAULT_TRANSCRIPTION_PROVIDER = "local"
_DEFAULT_LOCAL_WHISPER_MODEL = "small"
_DEFAULT_LOCAL_WHISPER_DEVICE = "auto"
_DEFAULT_LOCAL_WHISPER_COMPUTE_TYPE = "auto"
_DEFAULT_LOCAL_WHISPER_LANGUAGE = "auto"
_DEFAULT_LOCAL_WHISPER_WARMUP = True
_DEFAULT_OPENAI_TRANSCRIPTION_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_OPENAI_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
_DEFAULT_LIVE_TIMEOUT_SECONDS = 3.0
_DEFAULT_THESPORTSDB_API_KEY = "123"
_DEFAULT_LOCAL_TTS_ENABLED = True
_DEFAULT_LOCAL_TTS_PROVIDER = "kokoro"
_DEFAULT_LOCAL_TTS_VOICE = "am_adam"
_DEFAULT_LOCAL_TTS_SPEED = 0.95
_DEFAULT_LOCAL_TTS_WARMUP = True
_DEFAULT_CORS_ALLOWED_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "https://localhost",
    "https://rocky-web-preview.vercel.app",
)
_DEFAULT_PUBLIC_APP_URL = "http://localhost:5173"
_DEFAULT_EMAIL_VERIFICATION_TTL_HOURS = 24
_DEFAULT_PASSWORD_RESET_TTL_MINUTES = 60
_DEFAULT_EMAIL_VERIFICATION_COOLDOWN_SECONDS = 60
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


def _required(name: str) -> str:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        raise MissingConfigurationError(f"{name} is not configured.")
    return raw.strip()


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero.")
    return value


def get_auth_action_token_pepper() -> bytes:
    return _required("AUTH_ACTION_TOKEN_PEPPER").encode("utf-8")


def get_public_app_url() -> str:
    configured = os.getenv("PUBLIC_APP_URL")
    if not configured and (os.getenv("APP_ENV") or "development").strip().lower() == "production":
        raise MissingConfigurationError("PUBLIC_APP_URL is not configured.")
    raw = (configured or _DEFAULT_PUBLIC_APP_URL).strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise RuntimeError("PUBLIC_APP_URL must be an HTTP(S) origin without a query or fragment.")
    return raw


def get_email_from() -> str:
    return _required("EMAIL_FROM")


def get_resend_api_key() -> str:
    return _required("RESEND_API_KEY")


def get_email_verification_ttl() -> timedelta:
    return timedelta(hours=_positive_int("EMAIL_VERIFICATION_TTL_HOURS", _DEFAULT_EMAIL_VERIFICATION_TTL_HOURS))


def get_password_reset_ttl() -> timedelta:
    return timedelta(minutes=_positive_int("PASSWORD_RESET_TTL_MINUTES", _DEFAULT_PASSWORD_RESET_TTL_MINUTES))


def get_email_verification_cooldown() -> timedelta:
    return timedelta(seconds=_positive_int("EMAIL_VERIFICATION_COOLDOWN_SECONDS", _DEFAULT_EMAIL_VERIFICATION_COOLDOWN_SECONDS))


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


def get_transcription_provider_name() -> str:
    return (
        os.getenv("TRANSCRIPTION_PROVIDER")
        or _DEFAULT_TRANSCRIPTION_PROVIDER
    ).strip().lower()


def get_local_whisper_model() -> str:
    return os.getenv("LOCAL_WHISPER_MODEL") or _DEFAULT_LOCAL_WHISPER_MODEL


def get_local_whisper_device() -> str:
    return os.getenv("LOCAL_WHISPER_DEVICE") or _DEFAULT_LOCAL_WHISPER_DEVICE


def get_local_whisper_compute_type() -> str:
    return (
        os.getenv("LOCAL_WHISPER_COMPUTE_TYPE")
        or _DEFAULT_LOCAL_WHISPER_COMPUTE_TYPE
    )


def get_local_whisper_language() -> str:
    return os.getenv("LOCAL_WHISPER_LANGUAGE") or _DEFAULT_LOCAL_WHISPER_LANGUAGE


def get_local_whisper_warmup() -> bool:
    return _env_bool("LOCAL_WHISPER_WARMUP", _DEFAULT_LOCAL_WHISPER_WARMUP)


def get_openai_transcription_api_key() -> str | None:
    raw = os.getenv("OPENAI_TRANSCRIPTION_API_KEY")
    if raw:
        return raw
    general = get_openai_api_key()
    general_base_url = os.getenv("OPENAI_BASE_URL")
    if general and (
        not general_base_url or _is_official_openai_url(general_base_url)
    ):
        return general
    tts_key = os.getenv("OPENAI_TTS_API_KEY")
    if (
        tts_key
        and _is_official_openai_url(get_openai_tts_base_url())
        and _is_official_openai_url(get_openai_transcription_base_url())
    ):
        return tts_key
    return None


def get_openai_transcription_base_url() -> str:
    return (
        os.getenv("OPENAI_TRANSCRIPTION_BASE_URL")
        or _DEFAULT_OPENAI_TRANSCRIPTION_BASE_URL
    )


def get_openai_transcription_model() -> str:
    return (
        os.getenv("OPENAI_TRANSCRIPTION_MODEL")
        or _DEFAULT_OPENAI_TRANSCRIPTION_MODEL
    )


def get_live_timeout_seconds() -> float:
    raw = os.getenv("LIVE_TIMEOUT_SECONDS")
    if not raw:
        return _DEFAULT_LIVE_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise RuntimeError(
            f"LIVE_TIMEOUT_SECONDS must be numeric, got {raw!r}"
        ) from exc


def get_news_api_key() -> str | None:
    return os.getenv("NEWS_API_KEY") or None


def get_finnhub_api_key() -> str | None:
    return os.getenv("FINNHUB_API_KEY") or None


def get_tavily_api_key() -> str | None:
    return os.getenv("TAVILY_API_KEY") or None


def get_geoapify_api_key() -> str | None:
    return os.getenv("GEOAPIFY_API_KEY") or None


def get_thesportsdb_api_key() -> str:
    return os.getenv("THESPORTSDB_API_KEY") or _DEFAULT_THESPORTSDB_API_KEY


def _is_official_openai_url(value: str) -> bool:
    normalized = value.strip().rstrip("/")
    return normalized == "https://api.openai.com/v1"


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


def get_local_tts_warmup() -> bool:
    return _env_bool("LOCAL_TTS_WARMUP", _DEFAULT_LOCAL_TTS_WARMUP)


def get_cors_allowed_origins() -> list[str]:
    raw = os.getenv("CORS_ALLOWED_ORIGINS")
    if raw is None or raw.strip() == "":
        return list(_DEFAULT_CORS_ALLOWED_ORIGINS)
    origins = [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]
    if not origins:
        raise RuntimeError("CORS_ALLOWED_ORIGINS must contain at least one origin.")
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or origin == "*"
        ):
            raise RuntimeError(
                "CORS_ALLOWED_ORIGINS entries must be HTTP(S) origins "
                "without credentials, paths, queries, or fragments."
            )
    return list(dict.fromkeys(origins))


def validate_auth_runtime_configuration() -> None:
    """Fail startup before login can encounter missing core auth secrets."""

    missing = [
        name
        for name in ("SECRET_KEY", "REFRESH_TOKEN_PEPPER")
        if not (os.getenv(name) or "").strip()
    ]
    if missing:
        raise MissingConfigurationError(
            "Missing required authentication configuration: "
            + ", ".join(missing)
            + "."
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
    "get_auth_action_token_pepper",
    "get_public_app_url",
    "get_email_from",
    "get_resend_api_key",
    "get_email_verification_ttl",
    "get_password_reset_ttl",
    "get_email_verification_cooldown",
    "get_openai_api_key",
    "get_openai_model",
    "get_openai_timeout_seconds",
    "get_openai_tts_api_key",
    "get_openai_tts_base_url",
    "get_openai_tts_model",
    "get_openai_tts_voice",
    "get_openai_tts_speed",
    "get_transcription_provider_name",
    "get_local_whisper_model",
    "get_local_whisper_device",
    "get_local_whisper_compute_type",
    "get_local_whisper_language",
    "get_local_whisper_warmup",
    "get_openai_transcription_api_key",
    "get_openai_transcription_base_url",
    "get_openai_transcription_model",
    "get_live_timeout_seconds",
    "get_news_api_key",
    "get_finnhub_api_key",
    "get_tavily_api_key",
    "get_geoapify_api_key",
    "get_thesportsdb_api_key",
    "get_local_tts_enabled",
    "get_local_tts_provider",
    "get_local_tts_voice",
    "get_local_tts_speed",
    "get_local_tts_warmup",
    "get_cors_allowed_origins",
    "validate_auth_runtime_configuration",
]
