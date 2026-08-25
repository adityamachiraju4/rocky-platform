"""Transcription capability tests.

Transcription accepts authenticated microphone audio, validates the upload,
and delegates speech-to-text to a provider boundary. Tests keep the provider
fake so no raw audio leaves the process.
"""
from __future__ import annotations

import uuid
import sys
from pathlib import Path
from collections.abc import AsyncIterator
from types import SimpleNamespace

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
from app.transcription import dependencies as transcription_dependencies
from app.transcription.dependencies import (
    get_local_whisper_transcription_provider,
    get_openai_transcription_provider,
    get_transcription_provider,
)
from app.transcription.local_whisper_provider import (
    LocalWhisperTranscriptionProvider,
)
from app.transcription.openai_provider import (
    OpenAITranscriptionProvider,
    _openai_error_metadata,
)
from app.transcription.provider import TranscriptionProviderError

SECRET = "test-secret-key"
PEPPER = "test-refresh-pepper"
PASSWORD = "correct horse battery"


class _FakeTranscriptionProvider:
    def __init__(self, result: str | Exception) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    async def transcribe(
        self, audio: bytes, *, filename: str, content_type: str
    ) -> str:
        self.calls.append(
            {
                "audio": audio,
                "filename": filename,
                "content_type": content_type,
            }
        )
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _use_fake_provider(
    result: str | Exception,
) -> _FakeTranscriptionProvider:
    provider = _FakeTranscriptionProvider(result)
    fastapi_app.dependency_overrides[get_transcription_provider] = (
        lambda: provider
    )
    return provider


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    monkeypatch.setenv("REFRESH_TOKEN_PEPPER", PEPPER)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_TTS_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_TTS_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_TRANSCRIPTION_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_TRANSCRIPTION_BASE_URL", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_PROVIDER", raising=False)
    monkeypatch.delenv("LOCAL_WHISPER_MODEL", raising=False)
    monkeypatch.delenv("LOCAL_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("LOCAL_WHISPER_COMPUTE_TYPE", raising=False)
    monkeypatch.delenv("LOCAL_WHISPER_LANGUAGE", raising=False)
    monkeypatch.delenv("LOCAL_WHISPER_WARMUP", raising=False)
    transcription_dependencies._local_whisper_provider = None
    transcription_dependencies._local_whisper_model = None
    transcription_dependencies._local_whisper_device = None
    transcription_dependencies._local_whisper_compute_type = None
    transcription_dependencies._local_whisper_language = None


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
        fastapi_app.dependency_overrides.pop(
            get_transcription_provider, None
        )
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
async def test_transcription_success_returns_text(ctx) -> None:
    provider = _use_fake_provider("Create a project called Atlas")
    client, sessionmaker = ctx
    headers = await _auth_headers(client, sessionmaker)

    resp = await client.post(
        "/transcribe",
        files={"audio": ("speech.webm", b"audio-bytes", "audio/webm")},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"text": "Create a project called Atlas"}
    assert provider.calls == [
        {
            "audio": b"audio-bytes",
            "filename": "speech.webm",
            "content_type": "audio/webm",
        }
    ]


@pytest.mark.asyncio
async def test_transcription_requires_authentication(ctx) -> None:
    _use_fake_provider("ignored")
    client, _ = ctx

    resp = await client.post(
        "/transcribe",
        files={"audio": ("speech.webm", b"audio-bytes", "audio/webm")},
    )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_transcription_rejects_empty_upload(ctx) -> None:
    provider = _use_fake_provider("ignored")
    client, sessionmaker = ctx
    headers = await _auth_headers(client, sessionmaker)

    resp = await client.post(
        "/transcribe",
        files={"audio": ("speech.webm", b"", "audio/webm")},
        headers=headers,
    )

    assert resp.status_code == 400
    assert provider.calls == []


@pytest.mark.asyncio
async def test_transcription_rejects_unsupported_upload(ctx) -> None:
    provider = _use_fake_provider("ignored")
    client, sessionmaker = ctx
    headers = await _auth_headers(client, sessionmaker)

    resp = await client.post(
        "/transcribe",
        files={"audio": ("speech.txt", b"hello", "text/plain")},
        headers=headers,
    )

    assert resp.status_code == 400
    assert provider.calls == []


@pytest.mark.asyncio
async def test_transcription_provider_failure_is_clean_503(ctx) -> None:
    _use_fake_provider(TranscriptionProviderError("boom"))
    client, sessionmaker = ctx
    headers = await _auth_headers(client, sessionmaker)

    resp = await client.post(
        "/transcribe",
        files={"audio": ("speech.webm", b"audio-bytes", "audio/webm")},
        headers=headers,
    )

    assert resp.status_code == 503
    assert resp.json()["detail"] == "Transcription is unavailable."


def test_transcription_provider_wiring_uses_openai_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_TRANSCRIPTION_API_KEY", "test-key")

    provider = get_openai_transcription_provider()

    assert isinstance(provider, OpenAITranscriptionProvider)


def test_transcription_provider_reuses_general_openai_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "general-key")

    provider = get_openai_transcription_provider()

    assert isinstance(provider, OpenAITranscriptionProvider)


def test_transcription_provider_reuses_official_tts_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_TTS_API_KEY", "tts-key")

    provider = get_openai_transcription_provider()

    assert isinstance(provider, OpenAITranscriptionProvider)


def test_transcription_provider_reuses_official_tts_key_when_general_gateway_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "gateway-key")
    monkeypatch.setenv("OPENAI_TTS_API_KEY", "tts-key")

    provider = get_openai_transcription_provider()

    assert isinstance(provider, OpenAITranscriptionProvider)


def test_transcription_specific_key_wins_over_gateway_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "gateway-key")
    monkeypatch.setenv("OPENAI_TRANSCRIPTION_API_KEY", "transcription-key")

    provider = get_openai_transcription_provider()

    assert isinstance(provider, OpenAITranscriptionProvider)


def test_transcription_provider_defaults_to_local() -> None:
    provider = get_transcription_provider()

    assert isinstance(provider, LocalWhisperTranscriptionProvider)


def test_local_whisper_language_defaults_to_english() -> None:
    assert settings.get_local_whisper_language() == "en"


def test_local_whisper_warmup_defaults_to_enabled() -> None:
    assert settings.get_local_whisper_warmup() is True


def test_local_whisper_language_can_be_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_WHISPER_LANGUAGE", "es")

    provider = get_local_whisper_transcription_provider()

    assert settings.get_local_whisper_language() == "es"
    assert isinstance(provider, LocalWhisperTranscriptionProvider)
    assert provider.language == "es"


def test_transcription_provider_reuses_cached_local_provider() -> None:
    provider = get_local_whisper_transcription_provider()

    assert get_local_whisper_transcription_provider() is provider


def test_transcription_provider_selects_openai_when_explicitly_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_TRANSCRIPTION_API_KEY", "test-key")

    provider = get_transcription_provider()

    assert isinstance(provider, OpenAITranscriptionProvider)


def test_transcription_openai_provider_wiring_allows_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "openai")

    assert get_transcription_provider() is None


def test_transcription_provider_does_not_reuse_gateway_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "gateway-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

    assert get_transcription_provider() is None


def test_transcription_provider_does_not_reuse_non_official_tts_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_TTS_API_KEY", "tts-key")
    monkeypatch.setenv("OPENAI_TTS_BASE_URL", "https://example.test/v1")

    assert get_transcription_provider() is None


@pytest.mark.asyncio
async def test_local_whisper_provider_success_reuses_model_and_deletes_temp_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Segment:
        text = " hello rocky"

    class _WhisperModel:
        init_count = 0
        seen_paths: list[Path] = []

        def __init__(self, *_args, **_kwargs) -> None:
            type(self).init_count += 1

        def transcribe(self, path: str, **_kwargs):
            temp_path = Path(path)
            assert temp_path.exists()
            type(self).seen_paths.append(temp_path)
            return [_Segment()], object()

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=_WhisperModel),
    )
    provider = LocalWhisperTranscriptionProvider(
        model_name="small",
        device="auto",
        compute_type="auto",
        language="en",
    )

    first = await provider.transcribe(
        b"audio-1",
        filename="speech.webm",
        content_type="audio/webm;codecs=opus",
    )
    second = await provider.transcribe(
        b"audio-2",
        filename="speech.mp4",
        content_type="audio/mp4",
    )

    assert first == "hello rocky"
    assert second == "hello rocky"
    assert _WhisperModel.init_count == 1
    assert len(_WhisperModel.seen_paths) == 2
    assert _WhisperModel.seen_paths[0].suffix == ".webm"
    assert _WhisperModel.seen_paths[1].suffix == ".mp4"
    assert all(not path.exists() for path in _WhisperModel.seen_paths)


@pytest.mark.asyncio
async def test_local_whisper_warmup_loads_and_reuses_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Segment:
        text = " hello rocky"

    class _WhisperModel:
        init_count = 0

        def __init__(self, *_args, **_kwargs) -> None:
            type(self).init_count += 1

        def transcribe(self, _path: str, **_kwargs):
            return [_Segment()], object()

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=_WhisperModel),
    )
    provider = LocalWhisperTranscriptionProvider(
        model_name="small",
        device="auto",
        compute_type="auto",
        language="en",
    )

    await provider.warm_up()
    text = await provider.transcribe(
        b"audio",
        filename="speech.webm",
        content_type="audio/webm",
    )

    assert text == "hello rocky"
    assert _WhisperModel.init_count == 1


@pytest.mark.asyncio
async def test_local_whisper_provider_passes_language_and_transcribe_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Segment:
        text = " can you hear me"

    class _WhisperModel:
        calls: list[dict[str, object]] = []

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def transcribe(self, path: str, **kwargs):
            type(self).calls.append({"path": path, **kwargs})
            return [_Segment()], object()

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=_WhisperModel),
    )
    provider = LocalWhisperTranscriptionProvider(
        model_name="small",
        device="auto",
        compute_type="auto",
        language="en",
    )

    text = await provider.transcribe(
        b"audio",
        filename="speech.webm",
        content_type="audio/webm",
    )

    assert text == "can you hear me"
    assert _WhisperModel.calls[0]["language"] == "en"
    assert _WhisperModel.calls[0]["task"] == "transcribe"


@pytest.mark.asyncio
async def test_local_whisper_provider_failure_deletes_temp_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _WhisperModel:
        seen_paths: list[Path] = []

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def transcribe(self, path: str, **_kwargs):
            temp_path = Path(path)
            assert temp_path.exists()
            type(self).seen_paths.append(temp_path)
            raise RuntimeError("decode failed")

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=_WhisperModel),
    )
    provider = LocalWhisperTranscriptionProvider(
        model_name="small",
        device="auto",
        compute_type="auto",
        language="en",
    )

    with pytest.raises(TranscriptionProviderError) as excinfo:
        await provider.transcribe(
            b"audio",
            filename="speech.ogg",
            content_type="audio/ogg",
        )

    assert excinfo.value.code == "local_whisper_failed"
    assert len(_WhisperModel.seen_paths) == 1
    assert not _WhisperModel.seen_paths[0].exists()


def test_openai_error_metadata_extracts_safe_status_type_and_message() -> None:
    class _OpenAIStyleError(Exception):
        status_code = 401
        code = "invalid_api_key"
        body = {
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_api_key",
                "message": "Incorrect API key provided.",
            }
        }

    metadata = _openai_error_metadata(_OpenAIStyleError("request failed"))

    assert metadata == {
        "status": 401,
        "error_type": "invalid_request_error",
        "error_code": "invalid_api_key",
        "upstream_message": "Incorrect API key provided.",
    }


def test_openai_error_metadata_extracts_flattened_error_body() -> None:
    class _OpenAIStyleError(Exception):
        status_code = 429
        code = "insufficient_quota"
        body = {
            "type": "insufficient_quota",
            "code": "insufficient_quota",
            "message": "You exceeded your current quota.",
        }

    metadata = _openai_error_metadata(_OpenAIStyleError("request failed"))

    assert metadata == {
        "status": 429,
        "error_type": "insufficient_quota",
        "error_code": "insufficient_quota",
        "upstream_message": "You exceeded your current quota.",
    }


@pytest.mark.asyncio
async def test_openai_provider_wraps_upstream_error_with_safe_message() -> None:
    class _OpenAIStyleError(Exception):
        status_code = 429
        code = "insufficient_quota"
        body = {
            "type": "insufficient_quota",
            "code": "insufficient_quota",
            "message": "You exceeded your current quota.",
        }

    class _Transcriptions:
        async def create(self, **_kwargs):
            raise _OpenAIStyleError("request failed")

    class _Audio:
        transcriptions = _Transcriptions()

    class _Client:
        audio = _Audio()

    provider = OpenAITranscriptionProvider(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini-transcribe",
        timeout_seconds=30,
    )
    provider._client = _Client()

    with pytest.raises(TranscriptionProviderError) as excinfo:
        await provider.transcribe(
            b"audio",
            filename="speech.webm",
            content_type="audio/webm",
        )

    assert excinfo.value.code == "request_failed"
    assert "You exceeded your current quota." in str(excinfo.value)
