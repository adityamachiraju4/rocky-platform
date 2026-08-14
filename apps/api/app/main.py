"""Application entrypoint and lifecycle for Project Rocky (Platform-004).

Bootstrap and lifecycle are centralized here:

* A single FastAPI ``lifespan`` async context manager owns startup and
  shutdown. Startup warms the database engine; shutdown disposes it so pooled
  connections are released cleanly on process exit. This replaces the
  deprecated ``@app.on_event`` hooks and gives lifecycle exactly one home.
* Two infrastructure probes are exposed:
    - ``GET /health``   liveness  — cheap, no dependencies. Answers
      "is the process up?"  Never touches the database, so a database outage
      does not cause a healthy process to be killed.
    - ``GET /ready``    readiness — executes ``SELECT 1`` against the
      database. Answers "can this instance serve traffic?"  Returns 503 when
      the dependency is unreachable so orchestrators stop routing to it.

Both probes hold no business logic; they are platform-layer infrastructure.
Capability routers are mounted elsewhere and are unaffected by this module.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.middleware_registry import configure_middleware
from app.identity.router import router as identity_router
from app.db.session import get_engine, get_sessionmaker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Centralized application startup and shutdown.

    Startup: warm the async engine (lazy singleton) so the first request does
    not pay construction cost, and so misconfiguration surfaces at boot.
    Shutdown: dispose the engine, releasing pooled connections. Schema
    management is owned by Alembic — startup never creates tables.
    """
    logger.info("Rocky API starting up")
    get_engine()  # construct the process-wide engine singleton
    try:
        yield
    finally:
        logger.info("Rocky API shutting down")
        await get_engine().dispose()


app = FastAPI(
    title="Rocky API",
    version="0.1.0",
    description="Backend API for the Rocky AI Operating System",
    lifespan=lifespan,
)

# Platform middleware is registered in one place (Platform-006).
configure_middleware(app)

# Capability routers (mounted after platform middleware).
app.include_router(identity_router)


@app.get("/")
def root():
    return {
        "message": "Welcome to Rocky",
        "status": "online",
        "version": "0.1.0",
    }


@app.get("/health")
def health():
    """Liveness probe — process is up. No dependency checks."""
    return {
        "status": "healthy",
    }


@app.get("/ready")
async def ready():
    """Readiness probe — verifies the database is reachable.

    Returns 200 when a trivial query succeeds, 503 otherwise so that
    orchestrators withhold traffic from an instance that cannot serve it.
    """
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - probe reports failure, never raises
        logger.exception("Readiness check failed")
        return JSONResponse(
            status_code=503,
            content={"status": "not ready"},
        )
    return {"status": "ready"}
