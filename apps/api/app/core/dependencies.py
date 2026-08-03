"""Centralized infrastructure dependency providers for Rocky (Platform-005).

Platform owns only *shared infrastructure* dependencies. Capability-specific
providers live inside their capability, never here. In particular this module
MUST NOT import from ``app.identity.*`` (or any other capability): the
dependency direction is Capability -> Platform -> Infrastructure, never the
reverse.

Only native FastAPI ``Depends()`` wiring is used - no container, registry,
service locator, or reflection.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_sessionmaker


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a request-scoped :class:`AsyncSession`.

    Intended for use with FastAPI's dependency injection. The session
    factory (and engine) are created lazily on the first request, never at
    import time. The session is closed automatically when the request
    completes; on an unhandled exception the transaction is rolled back
    before the session closes.
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


__all__ = ["get_session"]
