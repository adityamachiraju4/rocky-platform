"""Database foundation for Project Rocky.

Public API: ``Base``, ``async_engine``, ``AsyncSessionLocal``, ``get_session``.
Importing this package does not create the engine, read credentials, or
eagerly import the composition layer. ``Base`` is eager (no side effects);
the rest resolve lazily on first access (PEP 562), which also prevents an
import cycle between ``app.db`` and ``app.core.dependencies``.
"""
from __future__ import annotations

from typing import Any

from app.db.base import Base

__all__ = [
    "Base",
    "async_engine",
    "AsyncSessionLocal",
    "get_session",
]


def __getattr__(name: str) -> Any:
    if name in {"async_engine", "AsyncSessionLocal"}:
        from app.db import session as _session

        return getattr(_session, name)
    if name == "get_session":
        from app.db.database import get_session

        return get_session
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
