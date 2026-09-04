"""Tests for Platform-004 application lifecycle and health/readiness probes."""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture(autouse=True)
def disable_voice_warmup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("REFRESH_TOKEN_PEPPER", "test-refresh-pepper")
    monkeypatch.setenv("LOCAL_WHISPER_WARMUP", "false")
    monkeypatch.setenv("LOCAL_TTS_WARMUP", "false")


@pytest_asyncio.fixture
async def client() -> httpx.AsyncClient:
    """In-process client that drives the app's real lifespan."""
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as ac:
            yield ac


@pytest.mark.asyncio
async def test_health_is_liveness_only(client: httpx.AsyncClient) -> None:
    """/health returns 200 and reports healthy without touching the DB."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_ready_reports_ready_when_db_reachable(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/ready returns 200 when the database answers SELECT 1."""
    from app import main

    class ReachableSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def execute(self, statement) -> None:
            return None

    monkeypatch.setattr(main, "get_sessionmaker", lambda: ReachableSession)
    resp = await client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_root_unchanged(client: httpx.AsyncClient) -> None:
    """Root endpoint contract is preserved (no breaking change)."""
    resp = await client.get("/")
    assert resp.status_code == 200

    body = resp.json()

    assert body["status"] == "online"
    assert body["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_ready_returns_503_when_db_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/ready degrades to 503 when the DB dependency raises."""
    from app import main

    def _broken_sessionmaker():
        raise RuntimeError("db down")

    monkeypatch.setattr(main, "get_sessionmaker", _broken_sessionmaker)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)

        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as ac:
            resp = await ac.get("/ready")

    assert resp.status_code == 503
    assert resp.json() == {"status": "not ready"}


def test_required_auth_configuration_is_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import settings

    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("REFRESH_TOKEN_PEPPER", raising=False)

    with pytest.raises(settings.MissingConfigurationError) as excinfo:
        settings.validate_auth_runtime_configuration()

    assert "SECRET_KEY" in str(excinfo.value)
    assert "REFRESH_TOKEN_PEPPER" in str(excinfo.value)


def test_email_configuration_is_not_part_of_login_runtime_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import settings

    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("REFRESH_TOKEN_PEPPER", "test-refresh-pepper")
    monkeypatch.delenv("AUTH_ACTION_TOKEN_PEPPER", raising=False)
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    settings.validate_auth_runtime_configuration()
