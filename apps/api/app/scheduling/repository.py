"""Persistence operations for scheduled jobs."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scheduled_job import PENDING, RUNNING, ScheduledJob


class ScheduledJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, job: ScheduledJob) -> ScheduledJob:
        self._session.add(job)
        await self._session.flush()
        return job

    async def get(self, job_id: uuid.UUID) -> ScheduledJob | None:
        return await self._session.get(ScheduledJob, job_id)

    async def get_fresh(self, job_id: uuid.UUID) -> ScheduledJob | None:
        result = await self._session.execute(
            select(ScheduledJob)
            .where(ScheduledJob.id == job_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_owned(
        self, user_id: uuid.UUID, job_id: uuid.UUID
    ) -> ScheduledJob | None:
        result = await self._session.execute(
            select(ScheduledJob).where(
                ScheduledJob.id == job_id, ScheduledJob.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(
        self, user_id: uuid.UUID, key: str
    ) -> ScheduledJob | None:
        result = await self._session.execute(
            select(ScheduledJob).where(
                ScheduledJob.user_id == user_id,
                ScheduledJob.idempotency_key == key,
            )
        )
        return result.scalar_one_or_none()

    async def claim_due(
        self,
        *,
        now: datetime,
        worker_id: str,
        lease_duration: timedelta,
        limit: int,
    ) -> list[ScheduledJob]:
        claimable = or_(
            and_(ScheduledJob.status == PENDING, ScheduledJob.run_at <= now),
            and_(
                ScheduledJob.status == RUNNING,
                ScheduledJob.lease_expires_at.is_not(None),
                ScheduledJob.lease_expires_at <= now,
            ),
        )
        result = await self._session.execute(
            select(ScheduledJob)
            .where(claimable, ScheduledJob.attempts < ScheduledJob.max_attempts)
            .order_by(ScheduledJob.run_at, ScheduledJob.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        jobs = list(result.scalars().all())
        for job in jobs:
            job.status = RUNNING
            job.attempts += 1
            job.lease_owner = worker_id
            job.lease_expires_at = now + lease_duration
        await self._session.flush()
        return jobs
