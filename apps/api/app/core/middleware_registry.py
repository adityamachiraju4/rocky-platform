"""Centralized Platform middleware registration (Platform-006).

All Platform middleware is registered through :func:`configure_middleware` so
registration lives in exactly one place. Future Platform middleware should be
added here rather than scattered across the application.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.middleware import RequestContextMiddleware
from app.core.settings import get_cors_allowed_origins

_CORS_ALLOWED_METHODS = ["GET", "POST", "PATCH", "DELETE", "OPTIONS"]
_CORS_ALLOWED_HEADERS = [
    "Authorization",
    "Content-Type",
    "X-Correlation-ID",
    "X-Request-ID",
]


def configure_middleware(app: FastAPI) -> None:
    """Register all Platform middleware on the given application."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_allowed_origins(),
        allow_credentials=False,
        allow_methods=_CORS_ALLOWED_METHODS,
        allow_headers=_CORS_ALLOWED_HEADERS,
        expose_headers=["X-Correlation-ID", "X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)


__all__ = ["configure_middleware"]
