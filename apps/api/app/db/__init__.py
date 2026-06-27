"""Database foundation for Project Rocky.

Public API: ``Base``, ``async_engine``, ``AsyncSessionLocal``, ``get_session``.

Importing this package — or ``app.db.base`` — does **not** create the engine
or read database credentials. ``Base`` is imported eagerly because it carries
no side effects, while ``async_engine`` and ``AsyncSessionLocal`` are resolved
lazily on first attribute access so that ORM models, Alembic, and unit tests
can import freely without live database credentials.
"""
from __future__ import annotations

from typing import Any

from app.db.base import Base
from app.db.database import get_session

__all__ = [
    "Base",
    "async_engine",
    "AsyncSessionLocal",
    "get_session",
]


def __getattr__(name: str) -> Any:
    """Lazily resolve engine/session-factory attributes (PEP 562).

    Deferring these to ``app.db.session`` keeps ``from app.db import Base``
    and ``from app.db.base import Base`` free of engine initialization.
    """
    if name in {"async_engine", "AsyncSessionLocal"}:
        from app.db import session as _session

        return getattr(_session, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
