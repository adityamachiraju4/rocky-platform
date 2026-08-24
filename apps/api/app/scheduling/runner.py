"""One-pass scheduled job runner for a separately managed worker process."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from typing import Any

from app.models.scheduled_job import ScheduledJob
from app.scheduling.service import SchedulingService

JobHandler = Callable[[dict[str, Any]], Awaitable[None]]


class ScheduledJobRunner:
    def __init__(
        self,
        service: SchedulingService,
        handlers: Mapping[str, JobHandler],
        *,
        retry_delay: timedelta = timedelta(seconds=30),
    ) -> None:
        self._service = service
        self._handlers = dict(handlers)
        self._retry_delay = retry_delay

    async def run_once(self, worker_id: str, *, limit: int = 20) -> list[ScheduledJob]:
        jobs = await self._service.claim_due(worker_id, limit=limit)
        for job in jobs:
            job_id = job.id
            handler = self._handlers.get(job.job_type)
            if handler is None:
                await self._service.fail(
                    job_id,
                    worker_id,
                    f"No handler registered for {job.job_type!r}.",
                    retry_delay=self._retry_delay,
                )
                continue
            try:
                await handler(job.payload)
            except Exception as exc:  # noqa: BLE001 - durable retry boundary
                await self._service.rollback_handler_transaction()
                await self._service.fail(
                    job_id,
                    worker_id,
                    str(exc) or type(exc).__name__,
                    retry_delay=self._retry_delay,
                )
            else:
                await self._service.complete(job_id, worker_id)
        return jobs
