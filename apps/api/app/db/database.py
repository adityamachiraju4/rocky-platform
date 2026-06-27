"""FastAPI-compatible database session dependency."""
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
