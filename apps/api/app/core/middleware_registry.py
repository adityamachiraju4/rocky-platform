"""Centralized Platform middleware registration (Platform-006).

All Platform middleware is registered through :func:`configure_middleware` so
registration lives in exactly one place. Future Platform middleware should be
added here rather than scattered across the application.
"""
from __future__ import annotations

from fastapi import FastAPI

from app.core.middleware import RequestContextMiddleware


def configure_middleware(app: FastAPI) -> None:
    """Register all Platform middleware on the given application."""
    app.add_middleware(RequestContextMiddleware)


__all__ = ["configure_middleware"]
