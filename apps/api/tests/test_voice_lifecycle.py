"""Focused tests for process-isolated local voice worker lifecycles."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json
import os
from multiprocessing.connection import Connection
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from app.core import settings
from app.speech import dependencies as speech_dependencies
from app.speech.kokoro_provider import KokoroSpeechProvider, _KokoroWorker
from app.speech.provider import (
    FallbackSpeechProvider,
    SpeechAudio,
    SpeechProviderError,
)
from app.transcription import dependencies as transcription_dependencies
from app.transcription.local_whisper_provider import (
    LocalWhisperTranscriptionProvider,
)
from app.transcription.provider import TranscriptionProviderError
from app.voice.lifecycle import ManagedVoiceWorker
from app.voice.reaper import reap_idle_voice_models
from app.voice.worker import (
    PROTOCOL_VERSION,
    VoiceWorkerError,
    serve_voice_worker,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _StubWorker:
    def __init__(
        self,
        result: dict[str, object] | None = None,
        error: VoiceWorkerError | None = None,
    ) -> None:
        self.result = result or {}
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._running = False
        self._active_count = 0
        self.unload_result = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def active_count(self) -> int:
        return self._active_count

    async def request(
        self,
        operation: str,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        self._running = True
        self._active_count += 1
        self.calls.append((operation, dict(payload)))
        try:
            if self.error is not None:
                raise self.error
            return dict(self.result)
        finally:
            self._active_count -= 1

    async def unload_if_idle(self, _idle_seconds: float) -> bool:
        if self.unload_result:
            self._running = False
        return self.unload_result

    async def close(self) -> bool:
        was_running = self._running
        self._running = False
        return was_running


class _FakeSpeechProvider:
    def __init__(self, result: SpeechAudio | Exception) -> None:
        self.result = result
        self.calls: list[str] = []

    async def synthesize(
        self,
        text: str,
        *,
        language: str = "en",
    ) -> SpeechAudio:
        self.calls.append(text)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _test_worker_entry(connection: Connection) -> None:
    def _handle(
        operation: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if operation == "synthesize":
            text = payload.get("text")
            if text == "crash":
                os._exit(23)
            if text == "timeout":
                time.sleep(2)
            return {"content": b"RIFF-wav", "media_type": "audio/wav"}
        if operation == "crash":
            os._exit(23)
        if operation == "sleep":
            time.sleep(float(payload["seconds"]))
        if operation == "error":
            raise VoiceWorkerError(
                "test worker error",
                code="test_failure",
                error_type="TestFailure",
                reason="The test worker rejected the request.",
            )
        return {"value": payload.get("value")}

    serve_voice_worker(connection, _handle)


def _malformed_worker_entry(connection: Connection) -> None:
    connection.send({"protocol": PROTOCOL_VERSION, "type": "ready"})
    message = connection.recv()
    connection.send(
        {
            "protocol": PROTOCOL_VERSION,
            "type": "response",
            "request_id": f"wrong-{message['request_id']}",
            "ok": True,
            "result": {},
        }
    )
    connection.close()


def _manager(
    *,
    clock: _Clock | None = None,
    target=_test_worker_entry,
    startup: float = 2.0,
    request: float = 2.0,
    shutdown: float = 0.3,
) -> ManagedVoiceWorker:
    return ManagedVoiceWorker(
        name="test",
        target=target,
        startup_timeout_seconds=startup,
        request_timeout_seconds=request,
        shutdown_timeout_seconds=shutdown,
        monotonic=clock or time.monotonic,
    )


async def _wait_for_active(worker: ManagedVoiceWorker) -> None:
    for _ in range(100):
        if worker.active_count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("worker did not become active")


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
        "VOICE_WORKER_STARTUP_SECONDS",
        "VOICE_WORKER_REQUEST_SECONDS",
        "VOICE_WORKER_SHUTDOWN_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    assert settings.get_local_whisper_warmup() is False
    assert settings.get_local_tts_warmup() is False
    assert settings.get_voice_model_idle_seconds() == 600.0
    assert settings.get_voice_model_reaper_seconds() == 60.0
    assert settings.get_voice_worker_startup_seconds() == 10.0
    assert settings.get_voice_worker_request_seconds() == 300.0
    assert settings.get_voice_worker_shutdown_seconds() == 5.0


def test_voice_lifecycle_configuration_overrides_and_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = (
        "VOICE_MODEL_REAPER_SECONDS",
        "VOICE_WORKER_STARTUP_SECONDS",
        "VOICE_WORKER_REQUEST_SECONDS",
        "VOICE_WORKER_SHUTDOWN_SECONDS",
    )
    getters = (
        settings.get_voice_model_reaper_seconds,
        settings.get_voice_worker_startup_seconds,
        settings.get_voice_worker_request_seconds,
        settings.get_voice_worker_shutdown_seconds,
    )
    monkeypatch.setenv("VOICE_MODEL_IDLE_SECONDS", "30")
    assert settings.get_voice_model_idle_seconds() == 30.0
    for name, getter in zip(names, getters, strict=True):
        monkeypatch.setenv(name, "5")
        assert getter() == 5.0
        monkeypatch.setenv(name, "0")
        with pytest.raises(RuntimeError):
            getter()
    monkeypatch.setenv("VOICE_MODEL_IDLE_SECONDS", "-1")
    with pytest.raises(RuntimeError):
        settings.get_voice_model_idle_seconds()


def test_provider_construction_is_lazy_and_uses_spawn() -> None:
    speech = KokoroSpeechProvider(voice="am_adam", speed=0.95)
    transcription = LocalWhisperTranscriptionProvider(
        model_name="small",
        device="auto",
        compute_type="auto",
        language="en",
    )

    assert speech.is_pipeline_loaded is False
    assert transcription.is_model_loaded is False
    assert speech._worker.start_method == "spawn"
    assert transcription._worker.start_method == "spawn"


def test_parent_provider_imports_do_not_load_heavy_modules() -> None:
    code = """
import json
import sys
from app.speech.kokoro_provider import KokoroSpeechProvider
from app.transcription.local_whisper_provider import LocalWhisperTranscriptionProvider
KokoroSpeechProvider(voice='am_adam', speed=0.95)
LocalWhisperTranscriptionProvider(model_name='small', device='auto', compute_type='auto', language='en')
print(json.dumps(sorted({'torch', 'kokoro', 'faster_whisper', 'ctranslate2'} & set(sys.modules))))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert json.loads(completed.stdout) == []


@pytest.mark.asyncio
async def test_successful_kokoro_worker_response_maps_to_speech_audio() -> None:
    worker = _StubWorker({"content": b"RIFF-wav", "media_type": "audio/wav"})
    provider = KokoroSpeechProvider(
        voice="am_adam",
        speed=0.95,
        worker=worker,
    )

    audio = await provider.synthesize("Hello")

    assert audio == SpeechAudio(content=b"RIFF-wav", media_type="audio/wav")
    assert worker.calls == [
        (
            "synthesize",
            {
                "text": "Hello",
                "language": "en",
                "voice": "am_adam",
                "speed": 0.95,
            },
        )
    ]


@pytest.mark.asyncio
async def test_successful_whisper_worker_response_maps_all_metadata() -> None:
    worker = _StubWorker(
        {
            "text": "hello rocky",
            "language": "en",
            "language_probability": 0.98,
            "raw_language": "es",
            "raw_language_probability": 0.42,
        }
    )
    provider = LocalWhisperTranscriptionProvider(
        model_name="small",
        device="auto",
        compute_type="auto",
        language="auto",
        worker=worker,
    )

    result = await provider.transcribe(
        b"audio",
        filename="speech.webm",
        content_type="audio/webm",
    )

    assert result.text == "hello rocky"
    assert result.language == "en"
    assert result.language_probability == 0.98
    assert result.raw_language == "es"
    assert result.raw_language_probability == 0.42
    assert worker.calls[0][1]["audio"] == b"audio"


@pytest.mark.asyncio
async def test_speech_worker_error_maps_to_provider_error() -> None:
    worker = _StubWorker(
        error=VoiceWorkerError(
            "failed",
            code="worker_unavailable",
            error_type="EOFError",
            reason="The local worker exited.",
        )
    )
    provider = KokoroSpeechProvider(
        voice="am_adam",
        speed=0.95,
        worker=worker,
    )

    with pytest.raises(SpeechProviderError) as excinfo:
        await provider.synthesize("Hello")

    assert excinfo.value.code == "request_failed"
    assert excinfo.value.error_type == "EOFError"
    assert excinfo.value.reason == "The local worker exited."


@pytest.mark.asyncio
async def test_transcription_worker_error_and_empty_code_map_correctly() -> None:
    base = dict(
        model_name="small",
        device="auto",
        compute_type="auto",
        language="en",
    )
    failing = LocalWhisperTranscriptionProvider(
        **base,
        worker=_StubWorker(
            error=VoiceWorkerError(
                "failed",
                code="worker_timeout",
                error_type="TimeoutError",
                reason="Timed out.",
            )
        ),
    )
    empty = LocalWhisperTranscriptionProvider(
        **base,
        worker=_StubWorker(
            error=VoiceWorkerError(
                "empty",
                code="empty_transcription",
                error_type="EmptyTranscription",
                reason="Blank transcript.",
            )
        ),
    )
    unavailable = LocalWhisperTranscriptionProvider(
        **base,
        worker=_StubWorker(
            error=VoiceWorkerError(
                "unavailable",
                code="local_whisper_unavailable",
                error_type="ImportError",
                reason="Runtime unavailable.",
            )
        ),
    )

    with pytest.raises(TranscriptionProviderError) as failed:
        await failing.transcribe(b"a", filename="a.wav", content_type="audio/wav")
    with pytest.raises(TranscriptionProviderError) as blank:
        await empty.transcribe(b"a", filename="a.wav", content_type="audio/wav")
    with pytest.raises(TranscriptionProviderError) as missing:
        await unavailable.transcribe(
            b"a", filename="a.wav", content_type="audio/wav"
        )

    assert failed.value.code == "local_whisper_failed"
    assert blank.value.code == "empty_transcription"
    assert missing.value.code == "local_whisper_unavailable"


@pytest.mark.asyncio
async def test_malformed_provider_results_are_typed_failures() -> None:
    speech = KokoroSpeechProvider(
        voice="am_adam",
        speed=0.95,
        worker=_StubWorker({"content": "not-bytes", "media_type": "audio/wav"}),
    )
    transcription = LocalWhisperTranscriptionProvider(
        model_name="small",
        device="auto",
        compute_type="auto",
        language="en",
        worker=_StubWorker({"text": "hello", "language_probability": "high"}),
    )

    with pytest.raises(SpeechProviderError) as speech_error:
        await speech.synthesize("Hello")
    with pytest.raises(TranscriptionProviderError) as transcription_error:
        await transcription.transcribe(
            b"a", filename="a.wav", content_type="audio/wav"
        )

    assert speech_error.value.code == "request_failed"
    assert transcription_error.value.code == "local_whisper_failed"


@pytest.mark.asyncio
async def test_worker_is_lazy_reused_and_closed() -> None:
    worker = _manager()
    assert worker.is_running is False
    assert worker.start_count == 0
    try:
        assert await worker.request("echo", {"value": "first"}) == {
            "value": "first"
        }
        assert await worker.request("echo", {"value": "second"}) == {
            "value": "second"
        }
        assert worker.is_running is True
        assert worker.start_count == 1
    finally:
        assert await worker.close() is True
    assert worker.is_running is False


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_receive_each_others_responses() -> None:
    worker = _manager()
    try:
        first, second = await asyncio.gather(
            worker.request("sleep", {"seconds": 0.1, "value": "first"}),
            worker.request("echo", {"value": "second"}),
        )
    finally:
        await worker.close()

    assert first == {"value": "first"}
    assert second == {"value": "second"}


@pytest.mark.asyncio
async def test_cancelled_queued_request_cannot_start_a_late_worker() -> None:
    worker = _manager(request=2, shutdown=0.1)
    active = asyncio.create_task(
        worker.request("sleep", {"seconds": 1, "value": "active"})
    )
    await _wait_for_active(worker)
    queued = asyncio.create_task(worker.request("echo", {"value": "queued"}))
    await asyncio.sleep(0.05)
    queued.cancel()

    with pytest.raises(asyncio.CancelledError):
        await queued
    active_result = await asyncio.gather(active, return_exceptions=True)
    await asyncio.sleep(0.05)

    assert isinstance(active_result[0], VoiceWorkerError)
    assert worker.start_count == 1
    assert worker.is_running is False
    await worker.close()


@pytest.mark.asyncio
async def test_idle_worker_terminates_and_next_request_starts_fresh_worker() -> None:
    clock = _Clock()
    worker = _manager(clock=clock)
    try:
        await worker.request("echo", {"value": 1})
        first_start_count = worker.start_count
        clock.advance(599)
        assert await worker.unload_if_idle(600) is False
        clock.advance(1)
        assert await worker.unload_if_idle(600) is True
        assert worker.is_running is False
        await worker.request("echo", {"value": 2})
        assert worker.start_count == first_start_count + 1
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_active_worker_cannot_be_reaped() -> None:
    clock = _Clock()
    worker = _manager(clock=clock)
    request = asyncio.create_task(
        worker.request("sleep", {"seconds": 0.2, "value": "done"})
    )
    try:
        await _wait_for_active(worker)
        clock.advance(601)
        assert await worker.unload_if_idle(600) is False
        assert await request == {"value": "done"}
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_worker_crash_eof_and_malformed_response_are_typed() -> None:
    crashed = _manager()
    malformed = _manager(target=_malformed_worker_entry)
    try:
        with pytest.raises(VoiceWorkerError) as crash_error:
            await crashed.request("crash", {})
        with pytest.raises(VoiceWorkerError) as malformed_error:
            await malformed.request("echo", {})
    finally:
        await crashed.close()
        await malformed.close()

    assert crash_error.value.code == "worker_unavailable"
    assert malformed_error.value.code == "worker_protocol_error"


@pytest.mark.asyncio
async def test_worker_timeout_discards_child_and_allows_restart() -> None:
    worker = _manager(request=0.05, shutdown=0.1)
    try:
        with pytest.raises(VoiceWorkerError) as excinfo:
            await worker.request("sleep", {"seconds": 2, "value": "late"})
        assert excinfo.value.code == "worker_timeout"
        assert worker.is_running is False
        assert await worker.request("echo", {"value": "fresh"}) == {
            "value": "fresh"
        }
        assert worker.start_count == 2
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_real_worker_crash_and_timeout_map_to_speech_provider_errors() -> None:
    crash_worker = _manager()
    timeout_worker = _manager(request=0.05, shutdown=0.1)
    crashed = KokoroSpeechProvider(
        voice="am_adam",
        speed=0.95,
        worker=crash_worker,
    )
    timed_out = KokoroSpeechProvider(
        voice="am_adam",
        speed=0.95,
        worker=timeout_worker,
    )
    try:
        with pytest.raises(SpeechProviderError) as crash_error:
            await crashed.synthesize("crash")
        with pytest.raises(SpeechProviderError) as timeout_error:
            await timed_out.synthesize("timeout")
    finally:
        await crash_worker.close()
        await timeout_worker.close()

    assert crash_error.value.code == "request_failed"
    assert crash_error.value.error_type in {
        "EOFError",
        "ConnectionResetError",
        "BrokenPipeError",
        "OSError",
    }
    assert timeout_error.value.code == "request_failed"
    assert timeout_error.value.error_type == "TimeoutError"


@pytest.mark.asyncio
async def test_failed_graceful_shutdown_forces_child_within_bound() -> None:
    worker = _manager(request=10, shutdown=0.15)
    request = asyncio.create_task(
        worker.request("sleep", {"seconds": 5, "value": "late"})
    )
    await _wait_for_active(worker)
    started = time.monotonic()
    assert await worker.close() is True
    elapsed = time.monotonic() - started
    result = await asyncio.gather(request, return_exceptions=True)

    assert elapsed < 1
    assert isinstance(result[0], VoiceWorkerError)
    assert worker.is_running is False


@pytest.mark.asyncio
async def test_warmup_targets_child_worker_only() -> None:
    speech_worker = _StubWorker()
    transcription_worker = _StubWorker()
    speech = KokoroSpeechProvider(
        voice="am_adam",
        speed=0.95,
        worker=speech_worker,
    )
    transcription = LocalWhisperTranscriptionProvider(
        model_name="small",
        device="auto",
        compute_type="auto",
        language="en",
        worker=transcription_worker,
    )

    await asyncio.gather(speech.warm_up(), transcription.warm_up())

    assert speech_worker.calls[0][0] == "warmup"
    assert transcription_worker.calls[0][0] == "warmup"
    assert speech.is_pipeline_loaded is True
    assert transcription.is_model_loaded is True


@pytest.mark.asyncio
async def test_openai_fallback_survives_local_worker_failure() -> None:
    local = KokoroSpeechProvider(
        voice="am_adam",
        speed=0.95,
        worker=_StubWorker(
            error=VoiceWorkerError(
                "crashed",
                code="worker_unavailable",
                error_type="EOFError",
                reason="The local worker exited.",
            )
        ),
    )
    remote = _FakeSpeechProvider(
        SpeechAudio(content=b"remote", media_type="audio/mpeg")
    )
    provider = FallbackSpeechProvider((("kokoro", local), ("openai", remote)))

    audio = await provider.synthesize("Hello")

    assert audio.content == b"remote"
    assert audio.provider == "openai"
    assert remote.calls == ["Hello"]


def test_kokoro_child_output_contract_uses_request_local_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    result = _KokoroWorker().handle(
        "synthesize",
        {
            "text": "Hello",
            "language": "en",
            "voice": "am_adam",
            "speed": 0.95,
        },
    )

    assert result == {"content": b"RIFF-fake-wav", "media_type": "audio/wav"}


@pytest.mark.asyncio
async def test_reaper_does_not_construct_unused_providers() -> None:
    assert transcription_dependencies._local_whisper_provider is None
    assert speech_dependencies._kokoro_provider is None

    assert await reap_idle_voice_models(idle_seconds=600) == (False, False)

    assert transcription_dependencies._local_whisper_provider is None
    assert speech_dependencies._kokoro_provider is None


@pytest.mark.asyncio
async def test_reaper_checks_existing_workers_independently() -> None:
    whisper_worker = _StubWorker()
    speech_worker = _StubWorker()
    whisper_worker.unload_result = True
    whisper = LocalWhisperTranscriptionProvider(
        model_name="small",
        device="auto",
        compute_type="auto",
        language="en",
        worker=whisper_worker,
    )
    speech = KokoroSpeechProvider(
        voice="am_adam",
        speed=0.95,
        worker=speech_worker,
    )
    transcription_dependencies._local_whisper_provider = whisper
    speech_dependencies._kokoro_provider = speech

    assert await reap_idle_voice_models(idle_seconds=600) == (True, False)
