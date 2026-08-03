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
