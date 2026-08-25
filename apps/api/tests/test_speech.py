"""Speech capability tests.

Speech renders already-authoritative text into audio. These tests keep the
provider fake and verify the endpoint remains authenticated, bounded, and
failure-isolated from Conversation.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.models  # noqa: F401  (populate Base.metadata before create_all)
from app.core.dependencies import get_session
from app.core import settings
from app.db.base import Base
from app.main import app as fastapi_app
from app.models.user import User
from app.speech.dependencies import get_openai_speech_provider, get_speech_provider
from app.speech.openai_provider import OpenAISpeechProvider
from app.speech.provider import (
    FallbackSpeechProvider,
    SpeechAudio,
    SpeechProviderError,
)
from app.speech.schemas import MAX_SPEECH_TEXT_CHARS

SECRET = "test-secret-key"
PEPPER = "test-refresh-pepper"
PASSWORD = "correct horse battery"


class _FakeSpeechProvider:
    def __init__(self, result: SpeechAudio | Exception) -> None:
        self._result = result
        self.calls: list[str] = []

    async def synthesize(self, text: str) -> SpeechAudio:
        self.calls.append(text)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _use_fake_provider(result: SpeechAudio | Exception) -> _FakeSpeechProvider:
    provider = _FakeSpeechProvider(result)
    fastapi_app.dependency_overrides[get_speech_provider] = lambda: provider
    return provider


def test_local_tts_defaults_are_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOCAL_TTS_ENABLED", raising=False)
    monkeypatch.delenv("LOCAL_TTS_PROVIDER", raising=False)
    monkeypatch.delenv("LOCAL_TTS_VOICE", raising=False)
    monkeypatch.delenv("LOCAL_TTS_SPEED", raising=False)
    monkeypatch.delenv("LOCAL_TTS_WARMUP", raising=False)

    assert settings.get_local_tts_enabled() is True
    assert settings.get_local_tts_provider() == "kokoro"
    assert settings.get_local_tts_voice() == "am_adam"
    assert settings.get_local_tts_speed() == 0.95
    assert settings.get_local_tts_warmup() is True


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    monkeypatch.setenv("REFRESH_TOKEN_PEPPER", PEPPER)
    monkeypatch.setenv("LOCAL_TTS_ENABLED", "false")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_TTS_API_KEY", raising=False)


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    fastapi_app.dependency_overrides[get_session] = _override_get_session
    try:
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            yield ac, sessionmaker
    finally:
        fastapi_app.dependency_overrides.pop(get_session, None)
        fastapi_app.dependency_overrides.pop(get_speech_provider, None)
        await engine.dispose()


async def _make_login_ready_user(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker,
) -> str:
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/identity/users",
        json={"email": email, "password": PASSWORD, "full_name": "Ada"},
    )
    assert resp.status_code == 201, resp.text
    uid = resp.json()["id"]
    async with sessionmaker() as s:
        await s.execute(
            update(User)
            .where(User.id == uuid.UUID(uid))
            .values(is_verified=True, is_active=True)
        )
        await s.commit()
    return email


async def _auth_headers(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker,
) -> dict[str, str]:
    email = await _make_login_ready_user(client, sessionmaker)
    resp = await client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_speech_generation_returns_audio(ctx) -> None:
    provider = _use_fake_provider(
        SpeechAudio(content=b"mp3-bytes", media_type="audio/mpeg")
    )
    client, sessionmaker = ctx
    headers = await _auth_headers(client, sessionmaker)

    resp = await client.post(
        "/speech",
        json={"text": "Yes. I can hear you."},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    assert resp.content == b"mp3-bytes"
    assert resp.headers["content-type"] == "audio/mpeg"
    assert provider.calls == ["Yes. I can hear you."]


@pytest.mark.asyncio
async def test_speech_rejects_empty_text(ctx) -> None:
    _use_fake_provider(SpeechAudio(content=b"nope", media_type="audio/mpeg"))
    client, sessionmaker = ctx
    headers = await _auth_headers(client, sessionmaker)

    resp = await client.post(
        "/speech",
        json={"text": "   "},
        headers=headers,
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_speech_rejects_oversized_text(ctx) -> None:
    _use_fake_provider(SpeechAudio(content=b"nope", media_type="audio/mpeg"))
    client, sessionmaker = ctx
    headers = await _auth_headers(client, sessionmaker)

    resp = await client.post(
        "/speech",
        json={"text": "x" * (MAX_SPEECH_TEXT_CHARS + 1)},
        headers=headers,
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_speech_provider_failure_does_not_break_conversation(ctx) -> None:
    _use_fake_provider(SpeechProviderError("boom"))
    client, sessionmaker = ctx
    headers = await _auth_headers(client, sessionmaker)

    speech_resp = await client.post(
        "/speech",
        json={"text": "This should degrade."},
        headers=headers,
    )
    convo_resp = await client.post(
        "/conversation",
        json={"message": "projects"},
        headers=headers,
    )

    assert speech_resp.status_code == 503
    assert convo_resp.status_code == 200, convo_resp.text
    assert convo_resp.json()["action"] == "project.list"


@pytest.mark.asyncio
async def test_missing_speech_provider_keeps_conversation_usable(ctx) -> None:
    client, sessionmaker = ctx
    headers = await _auth_headers(client, sessionmaker)

    speech_resp = await client.post(
        "/speech",
        json={"text": "This should be unavailable."},
        headers=headers,
    )
    convo_resp = await client.post(
        "/conversation",
        json={"message": "projects"},
        headers=headers,
    )

    assert speech_resp.status_code == 503
    assert convo_resp.status_code == 200, convo_resp.text
    assert convo_resp.json()["action"] == "project.list"


@pytest.mark.asyncio
async def test_speech_requires_authentication(ctx) -> None:
    _use_fake_provider(SpeechAudio(content=b"nope", media_type="audio/mpeg"))
    client, _ = ctx

    resp = await client.post("/speech", json={"text": "Hello"})

    assert resp.status_code == 401


def test_speech_provider_wiring_uses_openai_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_TTS_ENABLED", "false")
    monkeypatch.setenv("OPENAI_TTS_API_KEY", "test-key")

    provider = get_openai_speech_provider()

    assert isinstance(provider, OpenAISpeechProvider)


def test_speech_provider_wiring_allows_missing_key() -> None:
    assert get_speech_provider() is None


def test_speech_provider_does_not_reuse_gateway_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_TTS_ENABLED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "gateway-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

    assert get_speech_provider() is None


@pytest.mark.asyncio
async def test_provider_priority_uses_local_first() -> None:
    local = _FakeSpeechProvider(
        SpeechAudio(content=b"local", media_type="audio/wav")
    )
    openai = _FakeSpeechProvider(
        SpeechAudio(content=b"openai", media_type="audio/mpeg")
    )
    provider = FallbackSpeechProvider((("kokoro", local), ("openai", openai)))

    audio = await provider.synthesize("Hello")

    assert audio.content == b"local"
    assert audio.media_type == "audio/wav"
    assert local.calls == ["Hello"]
    assert openai.calls == []


@pytest.mark.asyncio
async def test_provider_priority_falls_back_to_openai() -> None:
    local = _FakeSpeechProvider(SpeechProviderError("local failed"))
    openai = _FakeSpeechProvider(
        SpeechAudio(content=b"openai", media_type="audio/mpeg")
    )
    provider = FallbackSpeechProvider((("kokoro", local), ("openai", openai)))

    audio = await provider.synthesize("Hello")

    assert audio.content == b"openai"
    assert local.calls == ["Hello"]
    assert openai.calls == ["Hello"]


@pytest.mark.asyncio
async def test_provider_priority_reports_unavailable_when_all_fail() -> None:
    local = _FakeSpeechProvider(SpeechProviderError("local failed"))
    provider = FallbackSpeechProvider((("kokoro", local),))

    with pytest.raises(SpeechProviderError):
        await provider.synthesize("Hello")
