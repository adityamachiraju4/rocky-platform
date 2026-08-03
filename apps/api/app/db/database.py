"""FastAPI-compatible database session dependency.

The provider is defined in :mod:`app.core.dependencies` (the centralized
composition layer, Platform-005). It is re-exported here so the historical
import path ``app.db.database.get_session`` — and the ``app.db`` package's
declared public API — continue to resolve to the same object.
"""
from __future__ import annotations

from app.core.dependencies import get_session

__all__ = ["get_session"]
