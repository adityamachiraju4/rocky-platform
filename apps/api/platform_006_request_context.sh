#!/usr/bin/env bash
# Platform-006: Request Context Infrastructure.
#
# Creates three Platform (infrastructure-only) modules under app/core/ and
# wires middleware registration into the Platform-004 bootstrap via a registry.
#
#   app/core/request_context.py     ContextVar storage + RequestContext + helpers
#   app/core/middleware.py          pure-ASGI RequestContextMiddleware
#   app/core/middleware_registry.py configure_middleware(app)
#   tests/test_request_context.py   acceptance tests (1-7)
#
# main.py is edited surgically: one import + one configure_middleware(app) call.
# Lifespan, /health, /ready, root, and routing are NOT modified.
#
# Zero Capability imports (Platform-005 boundary preserved). No logging,
# tracing, metrics, events, audit, or dependency providers introduced.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT/apps/api"

# --- 1. request_context.py -------------------------------------------------
cat > app/core/request_context.py <<'PY'
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
PY
echo "Wrote app/core/request_context.py"

# --- 2. middleware.py ------------------------------------------------------
cat > app/core/middleware.py <<'PY'
"""Pure-ASGI request context middleware for Project Rocky (Platform-006).

Deliberately implemented as a raw ASGI middleware rather than Starlette's
``BaseHTTPMiddleware``: the latter runs its ``dispatch`` in a separate task,
so ``ContextVar`` values set there do not reliably propagate to the endpoint.
A raw ASGI middleware sets the context in the same task that runs the route,
guaranteeing the helpers in ``request_context`` observe the correct values.

Responsibilities (infrastructure only):
  * generate a fresh Request ID per request;
  * preserve an incoming ``X-Correlation-ID`` header, else generate one;
  * store both in the request context for the request's lifetime;
  * emit both as response headers;
  * reset the context vars after the response via reset tokens, so no value
    leaks between concurrent requests sharing a worker.
"""
from __future__ import annotations

import uuid
from typing import Callable

from app.core.request_context import (
    reset_request_context,
    set_request_context,
)

_REQUEST_ID_HEADER = b"x-request-id"
_CORRELATION_ID_HEADER = b"x-correlation-id"


class RequestContextMiddleware:
    """ASGI middleware that establishes per-request context identifiers."""

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        # Only HTTP requests carry request context; pass through everything
        # else (lifespan, websocket) untouched.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())

        # Header names arrive lowercased as bytes in the ASGI scope.
        correlation_id: str | None = None
        for name, value in scope.get("headers", ()):
            if name == _CORRELATION_ID_HEADER:
                decoded = value.decode("latin-1").strip()
                if decoded:
                    correlation_id = decoded
                break
        if correlation_id is None:
            correlation_id = str(uuid.uuid4())

        tokens = set_request_context(request_id, correlation_id)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = [
                    (n, v)
                    for (n, v) in message.get("headers", [])
                    if n not in (_REQUEST_ID_HEADER, _CORRELATION_ID_HEADER)
                ]
                headers.append(
                    (_REQUEST_ID_HEADER, request_id.encode("latin-1"))
                )
                headers.append(
                    (
                        _CORRELATION_ID_HEADER,
                        correlation_id.encode("latin-1"),
                    )
                )
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            reset_request_context(tokens)


__all__ = ["RequestContextMiddleware"]
PY
echo "Wrote app/core/middleware.py"

# --- 3. middleware_registry.py --------------------------------------------
cat > app/core/middleware_registry.py <<'PY'
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
PY
echo "Wrote app/core/middleware_registry.py"

# --- 4. Surgical main.py edit: import + registry call ----------------------
python - <<'PY'
from pathlib import Path

p = Path("app/main.py")
src = p.read_text()

if "configure_middleware" in src:
    print("main.py already wired; skipping.")
    raise SystemExit(0)

imp_needle = "from app.db.session import get_engine, get_sessionmaker"
if imp_needle not in src:
    raise SystemExit("EXPECTED db.session import line in main.py - aborting, wire by hand.")
src = src.replace(
    imp_needle,
    "from app.core.middleware_registry import configure_middleware\n" + imp_needle,
    1,
)

app_needle = '    lifespan=lifespan,\n)\n'
if app_needle not in src:
    raise SystemExit("EXPECTED FastAPI(...) block ending 'lifespan=lifespan,\\n)' - aborting, wire by hand.")
src = src.replace(
    app_needle,
    app_needle + "\n# Platform middleware is registered in one place (Platform-006).\nconfigure_middleware(app)\n",
    1,
)

p.write_text(src)
print("Wired configure_middleware(app) into main.py")
PY

# --- 5. tests --------------------------------------------------------------
cat > tests/test_request_context.py <<'PY'
"""Platform-006 request context infrastructure tests.

Covers the sprint's acceptance criteria: request id generation, correlation id
preservation and generation, uniqueness across requests, context isolation
between concurrent requests, cleanup after the response, and continued correct
behavior of the existing /health and /ready probes.
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.request_context import (
    get_correlation_id,
    get_request_context,
    get_request_id,
)
from app.main import app


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.mark.asyncio
async def test_every_request_receives_a_request_id() -> None:
    async with _client() as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.headers.get("x-request-id")


@pytest.mark.asyncio
async def test_incoming_correlation_id_is_preserved() -> None:
    async with _client() as client:
        resp = await client.get(
            "/health", headers={"X-Correlation-ID": "corr-abc-123"}
        )
    assert resp.headers["x-correlation-id"] == "corr-abc-123"


@pytest.mark.asyncio
async def test_correlation_id_generated_when_absent() -> None:
    async with _client() as client:
        resp = await client.get("/health")
    correlation_id = resp.headers.get("x-correlation-id")
    assert correlation_id
    assert len(correlation_id) >= 8


@pytest.mark.asyncio
async def test_consecutive_requests_receive_different_request_ids() -> None:
    async with _client() as client:
        first = await client.get("/health")
        second = await client.get("/health")
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


@pytest.mark.asyncio
async def test_concurrent_requests_have_isolated_context() -> None:
    """Concurrent requests must not observe each other's identifiers."""

    async def one(client: AsyncClient, index: int) -> str:
        correlation_id = f"corr-{index}"
        resp = await client.get(
            "/health", headers={"X-Correlation-ID": correlation_id}
        )
        assert resp.headers["x-correlation-id"] == correlation_id
        return resp.headers["x-request-id"]

    async with _client() as client:
        request_ids = await asyncio.gather(
            *[one(client, i) for i in range(25)]
        )

    assert len(set(request_ids)) == 25


@pytest.mark.asyncio
async def test_context_is_cleared_after_the_request() -> None:
    """Reset tokens must clear the context once the response is sent."""
    async with _client() as client:
        await client.get("/health")

    assert get_request_id() is None
    assert get_correlation_id() is None
    snapshot = get_request_context()
    assert snapshot.request_id is None
    assert snapshot.correlation_id is None


@pytest.mark.asyncio
async def test_health_endpoint_still_behaves_correctly() -> None:
    async with _client() as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_ready_endpoint_still_behaves_correctly() -> None:
    async with _client() as client:
        resp = await client.get("/ready")
    assert resp.status_code in (200, 503)
    assert resp.json()["status"] in ("ready", "not ready")
PY
echo "Wrote tests/test_request_context.py"

echo "DONE."
