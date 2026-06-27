"""Async engine and session factory configuration.

Configuration is read from environment variables; credentials are never
hardcoded. The engine targets PostgreSQL via the asyncpg driver.

Lazy construction
-----------------
Importing this module does **not** build the engine or read database
credentials. The engine and session factory are created on first actual
use — either by accessing the module-level ``async_engine`` /
``AsyncSessionLocal`` attributes, or by calling :func:`get_engine` /
:func:`get_sessionmaker`. This keeps imports (ORM models, Alembic,
unit tests) free of any requirement for live database credentials.

Recognized environment variables:

* ``DATABASE_URL`` — full SQLAlchemy async URL. If set, it takes precedence
  over the individual ``POSTGRES_*`` variables below.
* ``POSTGRES_USER``
* ``POSTGRES_PASSWORD``
* ``POSTGRES_HOST`` (default ``localhost``)
* ``POSTGRES_PORT`` (default ``5432``)
* ``POSTGRES_DB``
* ``DB_ECHO`` — ``"true"`` to enable SQL echo (default off).
* ``DB_POOL_SIZE`` (default ``5``)
* ``DB_MAX_OVERFLOW`` (default ``10``)
* ``DB_POOL_PRE_PING`` — ``"true"`` to validate connections (default on).
"""
from __future__ import annotations

import os
from typing import Any

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_ASYNC_DRIVER = "postgresql+asyncpg"

# Process-wide singletons, created lazily on first use.
_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _build_database_url() -> URL:
    """Build the async database URL from the environment.

    Prefers ``DATABASE_URL`` when present, otherwise assembles the URL from
    discrete ``POSTGRES_*`` components. The asyncpg driver is enforced so the
    engine is always async-capable.
    """
    raw_url = os.getenv("DATABASE_URL")
    if raw_url:
        url = make_url(raw_url)
        # Normalize to the async driver if a sync/plain URL was provided.
        if url.drivername in {"postgresql", "postgres", "postgresql+psycopg2"}:
            url = url.set(drivername=_ASYNC_DRIVER)
        return url

    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    database = os.getenv("POSTGRES_DB")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port_raw = os.getenv("POSTGRES_PORT", "5432")

    missing = [
        name
        for name, value in (
            ("POSTGRES_USER", user),
            ("POSTGRES_PASSWORD", password),
            ("POSTGRES_DB", database),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing required database environment variables: "
            + ", ".join(missing)
            + ". Set DATABASE_URL or the POSTGRES_* variables."
        )

    try:
        port = int(port_raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise RuntimeError(
            f"POSTGRES_PORT must be an integer, got {port_raw!r}"
        ) from exc

    return URL.create(
        drivername=_ASYNC_DRIVER,
        username=user,
        password=password,
        host=host,
        port=port,
        database=database,
    )


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use.

    Database credentials are only read here, never at import time.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            _build_database_url(),
            echo=_env_bool("DB_ECHO", False),
            future=True,
            pool_pre_ping=_env_bool("DB_POOL_PRE_PING", True),
            pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide session factory, creating it on first use."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


def __getattr__(name: str) -> Any:
    """Lazily expose ``async_engine`` / ``AsyncSessionLocal`` (PEP 562).

    Accessing these module attributes constructs the underlying objects on
    demand, preserving the original public API while keeping plain imports
    of this module free of credential requirements.
    """
    if name == "async_engine":
        return get_engine()
    if name == "AsyncSessionLocal":
        return get_sessionmaker()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "async_engine",
    "AsyncSessionLocal",
    "get_engine",
    "get_sessionmaker",
]
