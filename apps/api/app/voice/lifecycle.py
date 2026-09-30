"""Race-safe lifecycle for one process-local heavyweight voice resource."""
from __future__ import annotations

import asyncio
import gc
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Generic, TypeVar, cast

logger = logging.getLogger(__name__)

ResourceT = TypeVar("ResourceT")


class IdleVoiceModel(Generic[ResourceT]):
    """Load one model on demand and detach it only while it is inactive.

    The same lock protects initialization, active-use accounting, and resource
    detachment. Inference runs outside the lock after incrementing the active
    count, so a reaper can never detach a resource that a request is using.
    """

    def __init__(
        self,
        *,
        loader: Callable[[], ResourceT],
        provider: str,
        model: str,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._loader = loader
        self._provider = provider
        self._model = model
        self._monotonic = monotonic
        self._resource: ResourceT | None = None
        self._lock = threading.Lock()
        self._active_count = 0
        self._last_used: float | None = None
        self._has_loaded = False

    @property
    def resource(self) -> ResourceT | None:
        with self._lock:
            return self._resource

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return self._resource is not None

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active_count

    @contextmanager
    def use(self) -> Iterator[ResourceT]:
        resource = self._acquire()
        try:
            yield resource
        finally:
            self._release()

    def _acquire(self) -> ResourceT:
        with self._lock:
            if self._resource is None:
                is_reload = self._has_loaded
                event = (
                    "voice_model_reload_started"
                    if is_reload
                    else "voice_model_load_started"
                )
                logger.info(
                    event,
                    extra={
                        "voice_provider": self._provider,
                        "voice_model": self._model,
                        "is_reload": is_reload,
                    },
                )
                started = time.perf_counter()
                try:
                    resource = self._loader()
                except Exception as exc:
                    logger.warning(
                        "voice_model_load_failed",
                        extra={
                            "voice_provider": self._provider,
                            "voice_model": self._model,
                            "is_reload": is_reload,
                            "error_type": exc.__class__.__name__,
                        },
                    )
                    raise
                self._resource = resource
                self._has_loaded = True
                self._last_used = self._monotonic()
                logger.info(
                    "voice_model_loaded",
                    extra={
                        "voice_provider": self._provider,
                        "voice_model": self._model,
                        "is_reload": is_reload,
                        "elapsed_ms": (time.perf_counter() - started) * 1000,
                    },
                )
            self._active_count += 1
            return cast(ResourceT, self._resource)

    def _release(self) -> None:
        with self._lock:
            self._active_count -= 1
            self._last_used = self._monotonic()

    async def unload_if_idle(self, idle_seconds: float) -> bool:
        return await asyncio.to_thread(
            self._unload_if_idle_sync,
            idle_seconds,
        )

    async def close(self) -> bool:
        return await asyncio.to_thread(self._close_sync)

    def _unload_if_idle_sync(self, idle_seconds: float) -> bool:
        with self._lock:
            if self._resource is None or self._last_used is None:
                return False
            now = self._monotonic()
            idle_age = max(0.0, now - self._last_used)
            if idle_age < idle_seconds:
                return False
            if self._active_count:
                logger.info(
                    "voice_model_unload_skipped_active",
                    extra={
                        "voice_provider": self._provider,
                        "voice_model": self._model,
                        "reason": "active_inference",
                        "idle_seconds": idle_age,
                        "active_count": self._active_count,
                    },
                )
                return False
            resource = self._detach_locked(
                reason="idle",
                idle_seconds=idle_age,
            )
            # Keep initialization excluded until the detached resource and any
            # provider-owned cycles are eligible for collection. This makes an
            # unload-versus-request race resolve to unload-then-reload.
            del resource
            gc.collect()
            return True

    def _close_sync(self) -> bool:
        with self._lock:
            if self._resource is None:
                return False
            if self._active_count:
                logger.info(
                    "voice_model_unload_skipped_active",
                    extra={
                        "voice_provider": self._provider,
                        "voice_model": self._model,
                        "reason": "shutdown_active_inference",
                        "active_count": self._active_count,
                    },
                )
                return False
            idle_age = (
                max(0.0, self._monotonic() - self._last_used)
                if self._last_used is not None
                else 0.0
            )
            resource = self._detach_locked(
                reason="shutdown",
                idle_seconds=idle_age,
            )
            del resource
            gc.collect()
            return True

    def _detach_locked(
        self,
        *,
        reason: str,
        idle_seconds: float,
    ) -> ResourceT:
        resource = cast(ResourceT, self._resource)
        self._resource = None
        self._last_used = None
        logger.info(
            "voice_model_unloaded",
            extra={
                "voice_provider": self._provider,
                "voice_model": self._model,
                "reason": reason,
                "idle_seconds": idle_seconds,
            },
        )
        return resource
