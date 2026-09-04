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

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.middleware_registry import configure_middleware
from app.core import settings
from app.auth.router import router as auth_router
from app.identity.router import router as identity_router
from app.projects.router import router as projects_router
from app.tasks.router import router as tasks_router
from app.activity.router import router as activity_router
from app.conversation.router import router as conversation_router
from app.speech.router import router as speech_router
from app.transcription.router import router as transcription_router
from app.transcription.dependencies import (
    warm_local_whisper_transcription_provider,
)
from app.speech.dependencies import warm_local_speech_provider
from app.reminders.router import router as reminders_router
from app.notifications.router import router as notifications_router
from app.notes.router import router as notes_router
from app.lists.router import router as lists_router
from app.db.session import get_engine, get_sessionmaker

app_logger = logging.getLogger("app")
app_logger.setLevel(logging.INFO)
if not app_logger.handlers:
    app_handler = logging.StreamHandler()
    app_handler.setFormatter(
        logging.Formatter("%(levelname)s %(name)s %(message)s")
    )
    app_logger.addHandler(app_handler)
app_logger.propagate = False
logger = logging.getLogger(__name__)


async def _warm_voice_models() -> None:
    if (
        settings.get_transcription_provider_name() == "local"
        and settings.get_local_whisper_warmup()
    ):
        try:
            await warm_local_whisper_transcription_provider()
        except Exception as exc:  # noqa: BLE001 - warm-up is best effort
            logger.warning(
                "Local Whisper warm-up failed: error_type=%s",
                exc.__class__.__name__,
            )
    if settings.get_local_tts_enabled() and settings.get_local_tts_warmup():
        try:
            await warm_local_speech_provider()
        except Exception as exc:  # noqa: BLE001 - warm-up is best effort
            logger.warning(
                "Local speech warm-up failed: error_type=%s",
                exc.__class__.__name__,
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Centralized application startup and shutdown.

    Startup: warm the async engine (lazy singleton) so the first request does
    not pay construction cost, and so misconfiguration surfaces at boot.
    Shutdown: dispose the engine, releasing pooled connections. Schema
    management is owned by Alembic — startup never creates tables.
    """
    logger.info("Rocky API starting up")
    try:
        settings.validate_auth_runtime_configuration()
    except settings.MissingConfigurationError as exc:
        logger.critical(
            "Rocky API startup configuration invalid: error_type=%s reason=%s",
            exc.__class__.__name__,
            str(exc),
        )
        raise
    try:
        get_engine()  # validate database config and construct the lazy engine
    except Exception as exc:
        logger.critical(
            "Rocky API database configuration invalid: error_type=%s",
            exc.__class__.__name__,
        )
        raise RuntimeError("Database configuration is invalid.") from exc
    voice_warmup_task = asyncio.create_task(_warm_voice_models())
    try:
        yield
    finally:
        if not voice_warmup_task.done():
            voice_warmup_task.cancel()
        with suppress(asyncio.CancelledError):
            await voice_warmup_task
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
app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(tasks_router)
app.include_router(activity_router)
app.include_router(conversation_router)
app.include_router(speech_router)
app.include_router(transcription_router)
app.include_router(reminders_router)
app.include_router(notifications_router)
app.include_router(notes_router)
app.include_router(lists_router)


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
    except Exception as exc:  # noqa: BLE001 - probe reports failure, never raises
        original = getattr(exc, "orig", None)
        logger.error(
            "Readiness database check failed: error_type=%s "
            "database_error_code=%s",
            exc.__class__.__name__,
            getattr(original, "sqlstate", None),
        )
        return JSONResponse(
            status_code=503,
            content={"status": "not ready"},
        )
    return {"status": "ready"}
