"""Request-scoped context storage for Project Rocky (Platform-006).

Infrastructure only. This module owns the request context and nothing else:
no logging, tracing, metrics, capability imports, FastAPI router knowledge,
or business logic. Storage uses the standard library ``contextvars`` module so
each concurrent request/task sees its own isolated values under asyncio.

Future Platform components (logging, tracing, audit, metrics) consume the
helpers here instead of reaching into FastAPI ``Request`` objects.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_correlation_id: ContextVar[str | None] = ContextVar(
    "correlation_id", default=None
)


@dataclass(frozen=True)
class RequestContext:
    """Immutable snapshot of the current request's context identifiers."""

    request_id: str | None
    correlation_id: str | None


def get_request_id() -> str | None:
    """Return the current Request ID, or ``None`` outside a request."""
    return _request_id.get()


def get_correlation_id() -> str | None:
    """Return the current Correlation ID, or ``None`` outside a request."""
    return _correlation_id.get()


def get_request_context() -> RequestContext:
    """Return an immutable snapshot of the current request context."""
    return RequestContext(
        request_id=_request_id.get(),
        correlation_id=_correlation_id.get(),
    )


def set_request_context(
    request_id: str, correlation_id: str
) -> tuple[Token, Token]:
    """Set both context vars, returning reset tokens for later cleanup.

    Intended for use by the request-context middleware only.
    """
    return (
        _request_id.set(request_id),
        _correlation_id.set(correlation_id),
    )


def reset_request_context(tokens: tuple[Token, Token]) -> None:
    """Reset both context vars using tokens from :func:`set_request_context`."""
    request_token, correlation_token = tokens
    _request_id.reset(request_token)
    _correlation_id.reset(correlation_token)


__all__ = [
    "RequestContext",
    "get_request_id",
    "get_correlation_id",
    "get_request_context",
    "set_request_context",
    "reset_request_context",
]
