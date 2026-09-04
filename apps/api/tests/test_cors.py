"""CORS policy tests for browser and Capacitor-native API calls."""
from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError

from app.auth.dependencies import get_auth_service
from app.core import settings
from app.main import app
from app.services.auth_service import InvalidCredentialsError


class RejectingAuthService:
    async def login(self, payload) -> None:
        raise InvalidCredentialsError


class BrokenDatabaseAuthService:
    async def login(self, payload) -> None:
        raise SQLAlchemyError("database unavailable")


@pytest.fixture(autouse=True)
def disable_voice_warmup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_WHISPER_WARMUP", "false")
    monkeypatch.setenv("LOCAL_TTS_WARMUP", "false")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("origin", "path"),
    [
        ("http://localhost:5173", "/auth/login"),
        ("https://rocky-web-preview.vercel.app", "/auth/login"),
        ("https://rocky-web-preview.vercel.app", "/identity/users"),
    ],
)
async def test_allowed_origin_preflight_succeeds(origin: str, path: str) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.options(
            path,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()


@pytest.mark.asyncio
async def test_unknown_origin_is_not_granted_cors() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.options(
            "/auth/login",
            headers={
                "Origin": "https://unknown.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )

    assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_normal_login_failure_semantics_are_unchanged() -> None:
    app.dependency_overrides[get_auth_service] = lambda: RejectingAuthService()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/auth/login",
                json={"email": "nobody@example.com", "password": "wrong password"},
            )
    finally:
        app.dependency_overrides.pop(get_auth_service, None)

    assert response.status_code == 401
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_login_database_failure_is_safe_503(caplog) -> None:
    app.dependency_overrides[get_auth_service] = lambda: BrokenDatabaseAuthService()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/auth/login",
                json={"email": "nobody@example.com", "password": "wrong password"},
            )
    finally:
        app.dependency_overrides.pop(get_auth_service, None)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AUTH_SERVICE_UNAVAILABLE"
    assert "phase=login" in caplog.text
    assert "wrong password" not in caplog.text


def test_cors_origins_are_parsed_and_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "https://preview.example, https://preview.example/,http://localhost:5173",
    )

    assert settings.get_cors_allowed_origins() == [
        "https://preview.example",
        "http://localhost:5173",
    ]


@pytest.mark.parametrize(
    "value",
    [",,,", "*", "https://example.com/path", "https://user:pass@example.com"],
)
def test_malformed_cors_configuration_fails_explicitly(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", value)

    with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
        settings.get_cors_allowed_origins()
