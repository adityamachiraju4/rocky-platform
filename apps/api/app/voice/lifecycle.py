"""Managed subprocess lifecycle for one heavyweight local voice worker."""
from __future__ import annotations

import asyncio
import logging
import multiprocessing
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from multiprocessing.connection import Connection
from multiprocessing.context import BaseContext
from multiprocessing.process import BaseProcess
from typing import Protocol

from app.voice.worker import PROTOCOL_VERSION, VoiceWorkerError

logger = logging.getLogger(__name__)

WorkerTarget = Callable[[Connection], None]


class VoiceWorkerClient(Protocol):
    @property
    def is_running(self) -> bool: ...

    @property
    def active_count(self) -> int: ...

    async def request(
        self,
        operation: str,
        payload: Mapping[str, object],
    ) -> dict[str, object]: ...

    async def unload_if_idle(self, idle_seconds: float) -> bool: ...

    async def close(self) -> bool: ...


class ManagedVoiceWorker:
    """Own a lazy spawn-context worker and serialize its private IPC channel."""

    def __init__(
        self,
        *,
        name: str,
        target: WorkerTarget,
        startup_timeout_seconds: float,
        request_timeout_seconds: float,
        shutdown_timeout_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
        context: BaseContext | None = None,
    ) -> None:
        self._name = name
        self._target = target
        self._startup_timeout_seconds = startup_timeout_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._monotonic = monotonic
        self._context = context or multiprocessing.get_context("spawn")
        self._request_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._process: BaseProcess | None = None
        self._connection: Connection | None = None
        self._active_count = 0
        self._last_used: float | None = None
        self._closed = False
        self._start_count = 0

    @property
    def start_method(self) -> str:
        return self._context.get_start_method()

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._process is not None and self._process.is_alive()

    @property
    def active_count(self) -> int:
        with self._state_lock:
            return self._active_count

    @property
    def start_count(self) -> int:
        with self._state_lock:
            return self._start_count

    async def request(
        self,
        operation: str,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        timeout = (
            self._startup_timeout_seconds
            + self._request_timeout_seconds
            + self._shutdown_timeout_seconds
        )
        cancelled = threading.Event()
        request_task = asyncio.create_task(
            asyncio.to_thread(
                self._request_sync,
                operation,
                dict(payload),
                cancelled,
            )
        )
        try:
            return await asyncio.wait_for(
                asyncio.shield(request_task),
                timeout=timeout,
            )
        except TimeoutError as exc:
            cancelled.set()
            await asyncio.to_thread(self._abort_current_worker, "timeout")
            request_task.cancel()
            raise VoiceWorkerError(
                "Voice worker request timed out.",
                code="worker_timeout",
                error_type="TimeoutError",
                reason="Local voice inference exceeded its bounded timeout.",
            ) from exc
        except asyncio.CancelledError:
            cancelled.set()
            await asyncio.to_thread(self._abort_current_worker, "cancelled")
            request_task.cancel()
            raise

    async def unload_if_idle(self, idle_seconds: float) -> bool:
        return await asyncio.to_thread(self._unload_if_idle_sync, idle_seconds)

    async def close(self) -> bool:
        with self._state_lock:
            self._closed = True
        return await asyncio.to_thread(self._close_sync)

    def _request_sync(
        self,
        operation: str,
        payload: dict[str, object],
        cancelled: threading.Event,
    ) -> dict[str, object]:
        with self._request_lock:
            self._raise_if_cancelled(cancelled)
            with self._state_lock:
                if self._closed:
                    raise VoiceWorkerError(
                        "Voice worker is closed.",
                        code="worker_closed",
                        error_type="WorkerClosed",
                        reason="The local voice worker has been shut down.",
                    )
                self._active_count += 1
            try:
                process, connection = self._ensure_worker_locked(cancelled)
                self._raise_if_cancelled(cancelled)
                request_id = uuid.uuid4().hex
                message = {
                    "protocol": PROTOCOL_VERSION,
                    "type": "request",
                    "request_id": request_id,
                    "operation": operation,
                    "payload": payload,
                }
                try:
                    connection.send(message)
                    if not connection.poll(self._request_timeout_seconds):
                        raise VoiceWorkerError(
                            "Voice worker request timed out.",
                            code="worker_timeout",
                            error_type="TimeoutError",
                            reason=(
                                "Local voice inference exceeded its bounded "
                                "timeout."
                            ),
                        )
                    response = connection.recv()
                except VoiceWorkerError:
                    self._discard_worker_locked(
                        process,
                        connection,
                        reason="request_timeout",
                    )
                    raise
                except (BrokenPipeError, EOFError, OSError) as exc:
                    self._discard_worker_locked(
                        process,
                        connection,
                        reason="worker_eof",
                    )
                    raise VoiceWorkerError(
                        "Voice worker became unavailable.",
                        code="worker_unavailable",
                        error_type=exc.__class__.__name__,
                        reason="The local voice worker exited unexpectedly.",
                    ) from exc
                return self._validate_response(
                    response,
                    request_id=request_id,
                    process=process,
                    connection=connection,
                )
            finally:
                with self._state_lock:
                    self._active_count -= 1
                    self._last_used = self._monotonic()

    def _ensure_worker_locked(
        self,
        cancelled: threading.Event,
    ) -> tuple[BaseProcess, Connection]:
        with self._state_lock:
            if self._closed:
                raise VoiceWorkerError(
                    "Voice worker is closed.",
                    code="worker_closed",
                    error_type="WorkerClosed",
                    reason="The local voice worker has been shut down.",
                )
            process = self._process
            connection = self._connection
            if (
                process is not None
                and connection is not None
                and process.is_alive()
            ):
                return process, connection
            self._process = None
            self._connection = None

        self._raise_if_cancelled(cancelled)

        if process is not None:
            self._stop_process(process, connection, graceful=False)

        parent_connection, child_connection = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=self._target,
            args=(child_connection,),
            name=f"rocky-{self._name}-worker",
            daemon=True,
        )
        logger.info(
            "voice_worker_starting",
            extra={"voice_provider": self._name},
        )
        try:
            process.start()
        except Exception as exc:
            parent_connection.close()
            child_connection.close()
            raise VoiceWorkerError(
                "Voice worker failed to start.",
                code="worker_start_failed",
                error_type=exc.__class__.__name__,
                reason="The local voice worker process could not start.",
            ) from exc
        child_connection.close()
        with self._state_lock:
            if cancelled.is_set():
                self._stop_process(process, parent_connection, graceful=False)
                raise self._cancelled_error()
            if self._closed:
                self._stop_process(process, parent_connection, graceful=False)
                raise VoiceWorkerError(
                    "Voice worker is closed.",
                    code="worker_closed",
                    error_type="WorkerClosed",
                    reason="The local voice worker has been shut down.",
                )
            self._process = process
            self._connection = parent_connection
            self._start_count += 1

        try:
            if not parent_connection.poll(self._startup_timeout_seconds):
                raise VoiceWorkerError(
                    "Voice worker startup timed out.",
                    code="worker_start_timeout",
                    error_type="TimeoutError",
                    reason="The local voice worker did not become ready in time.",
                )
            ready = parent_connection.recv()
        except VoiceWorkerError:
            self._discard_worker_locked(
                process,
                parent_connection,
                reason="startup_timeout",
            )
            raise
        except (EOFError, OSError) as exc:
            self._discard_worker_locked(
                process,
                parent_connection,
                reason="startup_eof",
            )
            raise VoiceWorkerError(
                "Voice worker failed during startup.",
                code="worker_start_failed",
                error_type=exc.__class__.__name__,
                reason="The local voice worker exited before becoming ready.",
            ) from exc
        if not (
            isinstance(ready, dict)
            and ready.get("protocol") == PROTOCOL_VERSION
            and ready.get("type") == "ready"
        ):
            self._discard_worker_locked(
                process,
                parent_connection,
                reason="malformed_startup",
            )
            raise VoiceWorkerError(
                "Voice worker returned an invalid startup message.",
                code="worker_protocol_error",
                error_type="MalformedResponse",
                reason="The local voice worker protocol handshake was invalid.",
            )
        logger.info(
            "voice_worker_started",
            extra={
                "voice_provider": self._name,
                "worker_pid": process.pid,
            },
        )
        return process, parent_connection

    @staticmethod
    def _raise_if_cancelled(cancelled: threading.Event) -> None:
        if cancelled.is_set():
            raise ManagedVoiceWorker._cancelled_error()

    @staticmethod
    def _cancelled_error() -> VoiceWorkerError:
        return VoiceWorkerError(
            "Voice worker request was cancelled.",
            code="worker_cancelled",
            error_type="CancelledError",
            reason="The local voice inference request was cancelled.",
        )

    def _validate_response(
        self,
        response: object,
        *,
        request_id: str,
        process: BaseProcess,
        connection: Connection,
    ) -> dict[str, object]:
        if not (
            isinstance(response, dict)
            and response.get("protocol") == PROTOCOL_VERSION
            and response.get("type") == "response"
            and response.get("request_id") == request_id
        ):
            self._discard_worker_locked(
                process,
                connection,
                reason="malformed_response",
            )
            raise VoiceWorkerError(
                "Voice worker returned an invalid response.",
                code="worker_protocol_error",
                error_type="MalformedResponse",
                reason="The local voice worker response did not match its request.",
            )
        if response.get("ok") is not True:
            if response.get("fatal") is True:
                self._discard_worker_locked(
                    process,
                    connection,
                    reason="fatal_worker_error",
                )
            raise VoiceWorkerError(
                "Voice worker inference failed.",
                code=_bounded_string(response.get("code"), "worker_error"),
                error_type=_bounded_string(
                    response.get("error_type"),
                    "WorkerError",
                ),
                reason=_bounded_string(
                    response.get("reason"),
                    "Local voice inference failed.",
                ),
            )
        result = response.get("result")
        if not isinstance(result, dict):
            self._discard_worker_locked(
                process,
                connection,
                reason="malformed_result",
            )
            raise VoiceWorkerError(
                "Voice worker returned an invalid result.",
                code="worker_protocol_error",
                error_type="MalformedResponse",
                reason="The local voice worker result was not an object.",
            )
        return result

    def _unload_if_idle_sync(self, idle_seconds: float) -> bool:
        if not self._request_lock.acquire(blocking=False):
            logger.info(
                "voice_worker_idle_stop_skipped_active",
                extra={
                    "voice_provider": self._name,
                    "reason": "active_request",
                },
            )
            return False
        try:
            with self._state_lock:
                if self._process is None or self._last_used is None:
                    return False
                idle_age = max(0.0, self._monotonic() - self._last_used)
                if idle_age < idle_seconds or self._active_count:
                    return False
            return self._stop_current_locked(
                reason="idle",
                graceful=True,
                idle_seconds=idle_age,
            )
        finally:
            self._request_lock.release()

    def _close_sync(self) -> bool:
        acquired = self._request_lock.acquire(
            timeout=self._shutdown_timeout_seconds / 2,
        )
        if acquired:
            try:
                return self._stop_current_locked(
                    reason="shutdown",
                    graceful=True,
                )
            finally:
                self._request_lock.release()
        logger.warning(
            "voice_worker_shutdown_escalated",
            extra={
                "voice_provider": self._name,
                "reason": "active_request_timeout",
            },
        )
        return self._abort_current_worker("shutdown_forced")

    def _abort_current_worker(self, reason: str) -> bool:
        return self._stop_current_locked(reason=reason, graceful=False)

    def _stop_current_locked(
        self,
        *,
        reason: str,
        graceful: bool,
        idle_seconds: float | None = None,
    ) -> bool:
        with self._state_lock:
            process = self._process
            connection = self._connection
            self._process = None
            self._connection = None
            self._last_used = None
        if process is None:
            if connection is not None:
                connection.close()
            return False
        self._stop_process(process, connection, graceful=graceful)
        logger.info(
            "voice_worker_stopped",
            extra={
                "voice_provider": self._name,
                "worker_pid": process.pid,
                "reason": reason,
                "idle_seconds": idle_seconds,
            },
        )
        return True

    def _discard_worker_locked(
        self,
        process: BaseProcess,
        connection: Connection,
        *,
        reason: str,
    ) -> None:
        with self._state_lock:
            owns_process = self._process is process
            if owns_process:
                self._process = None
                self._connection = None
                self._last_used = None
        if not owns_process:
            with suppress(OSError):
                connection.close()
            return
        self._stop_process(process, connection, graceful=False)
        logger.warning(
            "voice_worker_discarded",
            extra={
                "voice_provider": self._name,
                "worker_pid": process.pid,
                "reason": reason,
            },
        )

    def _stop_process(
        self,
        process: BaseProcess,
        connection: Connection | None,
        *,
        graceful: bool,
    ) -> None:
        deadline = time.monotonic() + self._shutdown_timeout_seconds
        graceful_phase = self._shutdown_timeout_seconds / 3
        if graceful and process.is_alive() and connection is not None:
            shutdown_id = uuid.uuid4().hex
            with suppress(BrokenPipeError, EOFError, OSError):
                connection.send(
                    {
                        "protocol": PROTOCOL_VERSION,
                        "type": "shutdown",
                        "request_id": shutdown_id,
                    }
                )
                if connection.poll(min(graceful_phase, _remaining(deadline))):
                    response = connection.recv()
                    if not (
                        isinstance(response, dict)
                        and response.get("type") == "shutdown_ack"
                        and response.get("request_id") == shutdown_id
                    ):
                        logger.warning(
                            "voice_worker_shutdown_ack_invalid",
                            extra={"voice_provider": self._name},
                        )
            process.join(min(graceful_phase, _remaining(deadline)))
        if process.is_alive():
            process.terminate()
            process.join(_remaining(deadline) / 2)
        if process.is_alive():
            logger.warning(
                "voice_worker_kill_escalated",
                extra={
                    "voice_provider": self._name,
                    "worker_pid": process.pid,
                },
            )
            process.kill()
            process.join(_remaining(deadline))
        else:
            process.join(0)
        if connection is not None:
            with suppress(OSError):
                connection.close()


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _bounded_string(value: object, default: str) -> str:
    if not isinstance(value, str) or not value:
        return default
    return value[:240]
