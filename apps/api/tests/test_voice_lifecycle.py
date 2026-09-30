"""Focused tests for lazy, independently unloadable local voice models."""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
from types import SimpleNamespace

import pytest

from app.core import settings
from app.speech import dependencies as speech_dependencies
from app.speech.kokoro_provider import KokoroSpeechProvider
from app.speech.provider import SpeechProviderError
from app.transcription import dependencies as transcription_dependencies
from app.transcription.local_whisper_provider import (
    LocalWhisperTranscriptionProvider,
)
from app.transcription.provider import TranscriptionProviderError
from app.voice.reaper import reap_idle_voice_models


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Segment:
    text = " hello rocky"


def _install_whisper(
    monkeypatch: pytest.MonkeyPatch,
    *,
    init_failures: int = 0,
    inference_error: bool = False,
    init_started: threading.Event | None = None,
    init_release: threading.Event | None = None,
    inference_started: threading.Event | None = None,
    inference_release: threading.Event | None = None,
) -> SimpleNamespace:
    state = SimpleNamespace(
        init_count=0,
        transcribe_count=0,
        init_failures=init_failures,
    )

    class _WhisperModel:
        def __init__(self, *_args, **_kwargs) -> None:
            state.init_count += 1
            if init_started is not None:
                init_started.set()
            if init_release is not None:
                assert init_release.wait(timeout=2)
            if state.init_failures:
                state.init_failures -= 1
                raise RuntimeError("model load failed")

        def transcribe(self, _path: str, **_kwargs):
            state.transcribe_count += 1
            if inference_started is not None:
                inference_started.set()
            if inference_release is not None:
                assert inference_release.wait(timeout=2)
            if inference_error:
                raise RuntimeError("inference failed")
            return [_Segment()], SimpleNamespace(
                language="en",
                language_probability=0.99,
            )

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=_WhisperModel),
    )
    return state


def _whisper_provider(clock: _Clock) -> LocalWhisperTranscriptionProvider:
    return LocalWhisperTranscriptionProvider(
        model_name="small",
        device="auto",
        compute_type="auto",
        language="en",
        monotonic=clock,
    )


def _install_kokoro(
    monkeypatch: pytest.MonkeyPatch,
    *,
    init_failures: int = 0,
    inference_error: bool = False,
    init_started: threading.Event | None = None,
    init_release: threading.Event | None = None,
    inference_started: threading.Event | None = None,
    inference_release: threading.Event | None = None,
) -> SimpleNamespace:
    state = SimpleNamespace(
        init_count=0,
        synthesize_count=0,
        init_failures=init_failures,
    )

    class _Pipeline:
        def __init__(self, **_kwargs) -> None:
            state.init_count += 1
            if init_started is not None:
                init_started.set()
            if init_release is not None:
                assert init_release.wait(timeout=2)
            if state.init_failures:
                state.init_failures -= 1
                raise RuntimeError("pipeline load failed")

        def __call__(self, _text: str) -> bytes:
            state.synthesize_count += 1
            if inference_started is not None:
                inference_started.set()
            if inference_release is not None:
                assert inference_release.wait(timeout=2)
            if inference_error:
                raise RuntimeError("synthesis failed")
            return b"wav-bytes"

    monkeypatch.setitem(
        sys.modules,
        "kokoro",
        SimpleNamespace(KPipeline=_Pipeline),
    )
    return state


def _kokoro_provider(
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,
) -> KokoroSpeechProvider:
    provider = KokoroSpeechProvider(
        voice="am_adam",
        speed=0.95,
        monotonic=clock,
    )
    monkeypatch.setattr(
        provider,
        "_synthesize_with_pipeline",
        lambda pipeline, text: pipeline(text),
    )
    return provider


@pytest.fixture(autouse=True)
def _reset_provider_singletons() -> None:
    transcription_dependencies._local_whisper_provider = None
    transcription_dependencies._local_whisper_model = None
    transcription_dependencies._local_whisper_device = None
    transcription_dependencies._local_whisper_compute_type = None
    transcription_dependencies._local_whisper_language = None
    speech_dependencies._kokoro_provider = None
    speech_dependencies._kokoro_provider_voice = None
    speech_dependencies._kokoro_provider_speed = None
    yield
    transcription_dependencies._local_whisper_provider = None
    speech_dependencies._kokoro_provider = None


def test_voice_lifecycle_configuration_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "LOCAL_WHISPER_WARMUP",
        "LOCAL_TTS_WARMUP",
        "VOICE_MODEL_IDLE_SECONDS",
        "VOICE_MODEL_REAPER_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    assert settings.get_local_whisper_warmup() is False
    assert settings.get_local_tts_warmup() is False
    assert settings.get_voice_model_idle_seconds() == 600.0
    assert settings.get_voice_model_reaper_seconds() == 60.0


def test_voice_lifecycle_configuration_overrides_and_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOICE_MODEL_IDLE_SECONDS", "30")
    monkeypatch.setenv("VOICE_MODEL_REAPER_SECONDS", "5")
    assert settings.get_voice_model_idle_seconds() == 30.0
    assert settings.get_voice_model_reaper_seconds() == 5.0

    monkeypatch.setenv("VOICE_MODEL_IDLE_SECONDS", "-1")
    with pytest.raises(RuntimeError):
        settings.get_voice_model_idle_seconds()
    monkeypatch.setenv("VOICE_MODEL_IDLE_SECONDS", "nan")
    with pytest.raises(RuntimeError):
        settings.get_voice_model_idle_seconds()
    monkeypatch.setenv("VOICE_MODEL_REAPER_SECONDS", "0")
    with pytest.raises(RuntimeError):
        settings.get_voice_model_reaper_seconds()


@pytest.mark.asyncio
async def test_whisper_first_use_loads_once_and_second_use_reuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    state = _install_whisper(monkeypatch)
    provider = _whisper_provider(clock)
    assert provider.is_model_loaded is False

    await provider.transcribe(b"a", filename="a.webm", content_type="audio/webm")
    await provider.transcribe(b"b", filename="b.webm", content_type="audio/webm")

    assert state.init_count == 1
    assert state.transcribe_count == 2
    assert provider.is_model_loaded is True


@pytest.mark.asyncio
async def test_whisper_concurrent_first_use_is_single_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    init_started = threading.Event()
    init_release = threading.Event()
    state = _install_whisper(
        monkeypatch,
        init_started=init_started,
        init_release=init_release,
    )
    provider = _whisper_provider(clock)

    first = asyncio.create_task(
        provider.transcribe(b"a", filename="a.webm", content_type="audio/webm")
    )
    assert await asyncio.to_thread(init_started.wait, 1)
    second = asyncio.create_task(
        provider.transcribe(b"b", filename="b.webm", content_type="audio/webm")
    )
    await asyncio.sleep(0)
    init_release.set()
    await asyncio.gather(first, second)

    assert state.init_count == 1


@pytest.mark.asyncio
async def test_whisper_idle_threshold_and_reload(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.voice.lifecycle")
    clock = _Clock()
    state = _install_whisper(monkeypatch)
    provider = _whisper_provider(clock)
    await provider.transcribe(b"a", filename="a.webm", content_type="audio/webm")

    clock.advance(599)
    assert await provider.unload_if_idle(600) is False
    clock.advance(1)
    assert await provider.unload_if_idle(600) is True
    assert provider.is_model_loaded is False

    await provider.transcribe(b"b", filename="b.webm", content_type="audio/webm")
    assert state.init_count == 2
    assert "voice_model_unloaded" in caplog.text
    assert "voice_model_reload_started" in caplog.text


@pytest.mark.asyncio
async def test_whisper_cannot_unload_during_active_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    started = threading.Event()
    release = threading.Event()
    _install_whisper(
        monkeypatch,
        inference_started=started,
        inference_release=release,
    )
    provider = _whisper_provider(clock)

    request = asyncio.create_task(
        provider.transcribe(b"a", filename="a.webm", content_type="audio/webm")
    )
    assert await asyncio.to_thread(started.wait, 1)
    clock.advance(601)
    assert await provider.unload_if_idle(600) is False
    assert provider.active_count == 1
    release.set()
    await request

    assert provider.active_count == 0
    clock.advance(600)
    assert await provider.unload_if_idle(600) is True


@pytest.mark.asyncio
async def test_whisper_request_racing_idle_unload_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    state = _install_whisper(monkeypatch)
    provider = _whisper_provider(clock)
    await provider.transcribe(b"a", filename="a.webm", content_type="audio/webm")
    clock.advance(601)

    unloaded, result = await asyncio.gather(
        provider.unload_if_idle(600),
        provider.transcribe(b"b", filename="b.webm", content_type="audio/webm"),
    )

    assert result.text == "hello rocky"
    assert provider.is_model_loaded is True
    assert state.init_count == (2 if unloaded else 1)


@pytest.mark.asyncio
async def test_whisper_failures_leave_lifecycle_reusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    state = _install_whisper(monkeypatch, init_failures=1)
    provider = _whisper_provider(clock)
    with pytest.raises(TranscriptionProviderError):
        await provider.transcribe(
            b"a", filename="a.webm", content_type="audio/webm"
        )
    assert provider.active_count == 0
    assert provider.is_model_loaded is False

    result = await provider.transcribe(
        b"b", filename="b.webm", content_type="audio/webm"
    )
    assert result.text == "hello rocky"
    assert state.init_count == 2

    _install_whisper(monkeypatch, inference_error=True)
    failing = _whisper_provider(clock)
    with pytest.raises(TranscriptionProviderError):
        await failing.transcribe(
            b"c", filename="c.webm", content_type="audio/webm"
        )
    assert failing.active_count == 0


@pytest.mark.asyncio
async def test_kokoro_first_use_loads_once_and_second_use_reuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    state = _install_kokoro(monkeypatch)
    provider = _kokoro_provider(monkeypatch, clock)
    assert provider.is_pipeline_loaded is False

    first = await provider.synthesize("Hello")
    second = await provider.synthesize("Again")

    assert first.content == second.content == b"wav-bytes"
    assert first.media_type == second.media_type == "audio/wav"
    assert state.init_count == 1
    assert state.synthesize_count == 2


@pytest.mark.asyncio
async def test_kokoro_concurrent_first_use_is_single_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    init_started = threading.Event()
    init_release = threading.Event()
    state = _install_kokoro(
        monkeypatch,
        init_started=init_started,
        init_release=init_release,
    )
    provider = _kokoro_provider(monkeypatch, clock)

    first = asyncio.create_task(provider.synthesize("Hello"))
    assert await asyncio.to_thread(init_started.wait, 1)
    second = asyncio.create_task(provider.synthesize("Again"))
    await asyncio.sleep(0)
    init_release.set()
    await asyncio.gather(first, second)

    assert state.init_count == 1


@pytest.mark.asyncio
async def test_kokoro_idle_threshold_and_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    state = _install_kokoro(monkeypatch)
    provider = _kokoro_provider(monkeypatch, clock)
    await provider.synthesize("Hello")

    clock.advance(599)
    assert await provider.unload_if_idle(600) is False
    clock.advance(1)
    assert await provider.unload_if_idle(600) is True
    assert provider.is_pipeline_loaded is False

    await provider.synthesize("Again")
    assert state.init_count == 2


@pytest.mark.asyncio
async def test_kokoro_cannot_unload_during_active_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    started = threading.Event()
    release = threading.Event()
    _install_kokoro(
        monkeypatch,
        inference_started=started,
        inference_release=release,
    )
    provider = _kokoro_provider(monkeypatch, clock)

    request = asyncio.create_task(provider.synthesize("Hello"))
    assert await asyncio.to_thread(started.wait, 1)
    clock.advance(601)
    assert await provider.unload_if_idle(600) is False
    assert provider.active_count == 1
    release.set()
    await request

    assert provider.active_count == 0
    clock.advance(600)
    assert await provider.unload_if_idle(600) is True


@pytest.mark.asyncio
async def test_kokoro_request_racing_idle_unload_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    state = _install_kokoro(monkeypatch)
    provider = _kokoro_provider(monkeypatch, clock)
    await provider.synthesize("Hello")
    clock.advance(601)

    unloaded, audio = await asyncio.gather(
        provider.unload_if_idle(600),
        provider.synthesize("Again"),
    )

    assert audio.content == b"wav-bytes"
    assert provider.is_pipeline_loaded is True
    assert state.init_count == (2 if unloaded else 1)


@pytest.mark.asyncio
async def test_kokoro_failures_leave_lifecycle_reusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    state = _install_kokoro(monkeypatch, init_failures=1)
    provider = _kokoro_provider(monkeypatch, clock)
    with pytest.raises(SpeechProviderError):
        await provider.synthesize("Hello")
    assert provider.active_count == 0
    assert provider.is_pipeline_loaded is False

    audio = await provider.synthesize("Again")
    assert audio.content == b"wav-bytes"
    assert state.init_count == 2

    _install_kokoro(monkeypatch, inference_error=True)
    failing = _kokoro_provider(monkeypatch, clock)
    with pytest.raises(SpeechProviderError):
        await failing.synthesize("Fail")
    assert failing.active_count == 0


@pytest.mark.asyncio
async def test_voice_models_unload_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    _install_whisper(monkeypatch)
    _install_kokoro(monkeypatch)
    whisper = _whisper_provider(clock)
    kokoro = _kokoro_provider(monkeypatch, clock)

    await whisper.transcribe(
        b"a", filename="a.webm", content_type="audio/webm"
    )
    await kokoro.synthesize("Hello")
    clock.advance(500)
    await whisper.transcribe(
        b"b", filename="b.webm", content_type="audio/webm"
    )
    clock.advance(101)

    assert await whisper.unload_if_idle(600) is False
    assert await kokoro.unload_if_idle(600) is True
    assert whisper.is_model_loaded is True
    assert kokoro.is_pipeline_loaded is False


@pytest.mark.asyncio
async def test_voice_models_unload_independently_in_reverse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    _install_whisper(monkeypatch)
    _install_kokoro(monkeypatch)
    whisper = _whisper_provider(clock)
    kokoro = _kokoro_provider(monkeypatch, clock)

    await whisper.transcribe(
        b"a", filename="a.webm", content_type="audio/webm"
    )
    await kokoro.synthesize("Hello")
    clock.advance(500)
    await kokoro.synthesize("Again")
    clock.advance(101)

    assert await whisper.unload_if_idle(600) is True
    assert await kokoro.unload_if_idle(600) is False
    assert whisper.is_model_loaded is False
    assert kokoro.is_pipeline_loaded is True


@pytest.mark.asyncio
async def test_explicit_warmup_uses_same_lifecycle_and_shutdown_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    whisper_state = _install_whisper(monkeypatch)
    kokoro_state = _install_kokoro(monkeypatch)
    whisper = _whisper_provider(clock)
    kokoro = _kokoro_provider(monkeypatch, clock)

    await asyncio.gather(whisper.warm_up(), kokoro.warm_up())
    assert whisper.is_model_loaded is True
    assert kokoro.is_pipeline_loaded is True
    assert whisper_state.init_count == 1
    assert kokoro_state.init_count == 1

    assert await whisper.close() is True
    assert await kokoro.close() is True
    assert whisper.is_model_loaded is False
    assert kokoro.is_pipeline_loaded is False


@pytest.mark.asyncio
async def test_kokoro_output_contract_uses_request_local_audio_buffers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()

    class _Pipeline:
        def __init__(self, **_kwargs) -> None:
            pass

        def __call__(self, *_args, **_kwargs):
            return iter(((None, None, b"one"), (None, None, b"two")))

    def _write(output, joined, sample_rate, *, format: str) -> None:
        assert joined == b"onetwo"
        assert sample_rate == 24000
        assert format == "WAV"
        output.write(b"RIFF-fake-wav")

    monkeypatch.setitem(
        sys.modules,
        "kokoro",
        SimpleNamespace(KPipeline=_Pipeline),
    )
    monkeypatch.setitem(
        sys.modules,
        "numpy",
        SimpleNamespace(concatenate=lambda segments: b"".join(segments)),
    )
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        SimpleNamespace(write=_write),
    )
    provider = KokoroSpeechProvider(
        voice="am_adam",
        speed=0.95,
        monotonic=clock,
    )

    audio = await provider.synthesize("Hello")

    assert audio.content == b"RIFF-fake-wav"
    assert audio.media_type == "audio/wav"


@pytest.mark.asyncio
async def test_reaper_does_not_construct_unused_providers() -> None:
    assert transcription_dependencies._local_whisper_provider is None
    assert speech_dependencies._kokoro_provider is None

    assert await reap_idle_voice_models(idle_seconds=600) == (False, False)

    assert transcription_dependencies._local_whisper_provider is None
    assert speech_dependencies._kokoro_provider is None


@pytest.mark.asyncio
async def test_reaper_checks_only_existing_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    whisper = _whisper_provider(clock)
    kokoro = _kokoro_provider(monkeypatch, clock)
    transcription_dependencies._local_whisper_provider = whisper
    speech_dependencies._kokoro_provider = kokoro
    calls: list[tuple[str, float]] = []

    async def _whisper_unload(idle_seconds: float) -> bool:
        calls.append(("whisper", idle_seconds))
        return False

    async def _kokoro_unload(idle_seconds: float) -> bool:
        calls.append(("kokoro", idle_seconds))
        return False

    monkeypatch.setattr(whisper, "unload_if_idle", _whisper_unload)
    monkeypatch.setattr(kokoro, "unload_if_idle", _kokoro_unload)

    assert await reap_idle_voice_models(idle_seconds=600) == (False, False)
    assert sorted(calls) == [("kokoro", 600), ("whisper", 600)]
    assert whisper.is_model_loaded is False
    assert kokoro.is_pipeline_loaded is False
