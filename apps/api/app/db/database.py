"""FastAPI-compatible database session dependency.

Re-exports ``get_session`` (defined in ``app.core.dependencies``, Platform-005)
lazily via PEP 562 so importing this module — and therefore ``app.db`` — does
not eagerly import ``app.core.dependencies``. That eager import created a cycle:
``app.core.dependencies`` -> (router import chain) -> ``app.db`` ->
``app.db.database`` -> ``app.core.dependencies``. Resolving on first access
breaks the cycle while preserving the ``app.db.database.get_session`` path.
"""
from __future__ import annotations

from typing import Any

__all__ = ["get_session"]


def __getattr__(name: str) -> Any:
    if name == "get_session":
        from app.core.dependencies import get_session

        return get_session
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
