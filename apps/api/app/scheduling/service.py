"""Authoritative transaction and lifecycle policy for scheduled work."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import UTC, Clock, ensure_utc, system_clock
from app.models.scheduled_job import (
    CANCELLED,
    COMPLETED,
    FAILED,
    PENDING,
    RUNNING,
    ScheduledJob,
)
from app.models.user import User
from app.scheduling.exceptions import (
    IdempotencyConflictError,
    InvalidJobTransitionError,
    ScheduledJobNotFoundError,
    ScheduledTimeInPastError,
    WorkerLeaseError,
)
from app.scheduling.repository import ScheduledJobRepository
from app.scheduling.schemas import ScheduledJobCreate


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return ensure_utc(value)


class SchedulingService:
    def __init__(self, session: AsyncSession, *, clock: Clock = system_clock) -> None:
        self._session = session
        self._clock = clock
        self._jobs = ScheduledJobRepository(session)

    def _now(self) -> datetime:
        return ensure_utc(self._clock.now())

    async def schedule(
        self, current_user: User, data: ScheduledJobCreate
    ) -> ScheduledJob:
        try:
            job = await self.schedule_in_transaction(current_user, data)
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            if data.idempotency_key is None:
                raise
            run_at = ensure_utc(data.run_at)
            existing = await self._jobs.get_by_idempotency_key(
                current_user.id, data.idempotency_key
            )
            if existing is None:
                raise
            if not self._matches_request(existing, data, run_at):
                raise IdempotencyConflictError(data.idempotency_key) from None
            return existing
        await self._session.refresh(job)
        return job

    async def schedule_in_transaction(
        self, current_user: User, data: ScheduledJobCreate
    ) -> ScheduledJob:
        """Add scheduled work without committing the caller's transaction."""
        run_at = ensure_utc(data.run_at)
        if run_at < self._now():
            raise ScheduledTimeInPastError("Scheduled time is in the past.")

        if data.idempotency_key:
            existing = await self._jobs.get_by_idempotency_key(
                current_user.id, data.idempotency_key
            )
            if existing is not None:
                if not self._matches_request(existing, data, run_at):
                    raise IdempotencyConflictError(data.idempotency_key)
                return existing

        job = ScheduledJob(
            user_id=current_user.id,
            job_type=data.job_type,
            payload=data.payload,
            run_at=run_at,
            idempotency_key=data.idempotency_key,
            max_attempts=data.max_attempts,
        )
        return await self._jobs.add(job)

    @staticmethod
    def _matches_request(
        job: ScheduledJob, data: ScheduledJobCreate, run_at: datetime
    ) -> bool:
        return (
            job.job_type == data.job_type
            and job.payload == data.payload
            and _stored_utc(job.run_at) == run_at
        )

    async def claim_due(
        self,
        worker_id: str,
        *,
        limit: int = 20,
        lease_duration: timedelta = timedelta(seconds=30),
    ) -> list[ScheduledJob]:
        if not worker_id or len(worker_id) > 128:
            raise ValueError("worker_id must be 1-128 characters.")
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100.")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive.")
        jobs = await self._jobs.claim_due(
            now=self._now(),
            worker_id=worker_id,
            lease_duration=lease_duration,
            limit=limit,
        )
        await self._session.commit()
        return jobs

    async def complete(self, job_id: uuid.UUID, worker_id: str) -> ScheduledJob:
        current = await self._jobs.get_fresh(job_id)
        if current is None:
            raise ScheduledJobNotFoundError(str(job_id))
        if current.status == CANCELLED:
            return current
        job = await self._require_worker_lease(job_id, worker_id)
        job.status = COMPLETED
        job.completed_at = self._now()
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_error = None
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def fail(
        self,
        job_id: uuid.UUID,
        worker_id: str,
        error: str,
        *,
        retry_delay: timedelta = timedelta(seconds=30),
    ) -> ScheduledJob:
        if retry_delay < timedelta(0):
            raise ValueError("retry_delay cannot be negative.")
        current = await self._jobs.get_fresh(job_id)
        if current is None:
            raise ScheduledJobNotFoundError(str(job_id))
        if current.status == CANCELLED:
            return current
        job = await self._require_worker_lease(job_id, worker_id)
        job.last_error = error[:2000]
        job.lease_owner = None
        job.lease_expires_at = None
        if job.attempts >= job.max_attempts:
            job.status = FAILED
        else:
            job.status = PENDING
            job.run_at = self._now() + retry_delay
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def rollback_handler_transaction(self) -> None:
        """Discard partial handler work before recording a durable retry."""
        await self._session.rollback()

    async def cancel(
        self, current_user: User, job_id: uuid.UUID
    ) -> ScheduledJob:
        job = await self.cancel_in_transaction(current_user, job_id)
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def cancel_in_transaction(
        self, current_user: User, job_id: uuid.UUID
    ) -> ScheduledJob:
        """Cancel owned work without committing the caller's transaction."""
        job = await self._jobs.get_owned(current_user.id, job_id)
        if job is None:
            raise ScheduledJobNotFoundError(str(job_id))
        if job.status in {COMPLETED, FAILED, CANCELLED}:
            raise InvalidJobTransitionError(job.status)
        job.status = CANCELLED
        job.lease_owner = None
        job.lease_expires_at = None
        return job

    async def _require_worker_lease(
        self, job_id: uuid.UUID, worker_id: str
    ) -> ScheduledJob:
        job = await self._jobs.get(job_id)
        if job is None:
            raise ScheduledJobNotFoundError(str(job_id))
        if job.status != RUNNING or job.lease_owner != worker_id:
            raise WorkerLeaseError(str(job_id))
        lease_expires_at = job.lease_expires_at
        if lease_expires_at is None or _stored_utc(lease_expires_at) <= self._now():
            raise WorkerLeaseError(str(job_id))
        return job
