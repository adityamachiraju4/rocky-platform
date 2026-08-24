from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.scheduled_job import COMPLETED, FAILED, PENDING, RUNNING
from app.models.user import User
from app.scheduling.exceptions import (
    IdempotencyConflictError,
    ScheduledJobNotFoundError,
    ScheduledTimeInPastError,
    WorkerLeaseError,
)
from app.scheduling.runner import ScheduledJobRunner
from app.scheduling.schemas import ScheduledJobCreate
from app.scheduling.service import SchedulingService
import app.models  # noqa: F401


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


@pytest_asyncio.fixture
async def ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        user = User(
            email="scheduler@example.com",
            password_hash="hash",
            is_verified=True,
        )
        other = User(
            email="other@example.com",
            password_hash="hash",
            is_verified=True,
        )
        session.add_all([user, other])
        await session.commit()
        await session.refresh(user)
        await session.refresh(other)
        clock = MutableClock(datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc))
        yield session, user, other, clock
    await engine.dispose()


def job_input(
    run_at: datetime,
    *,
    key: str | None = None,
    max_attempts: int = 3,
    payload: dict | None = None,
) -> ScheduledJobCreate:
    return ScheduledJobCreate(
        job_type="reminder.due",
        payload=payload or {"reminder_id": "r-1"},
        run_at=run_at,
        idempotency_key=key,
        max_attempts=max_attempts,
    )


@pytest.mark.asyncio
async def test_schedule_persists_utc_job(ctx) -> None:
    session, user, _, clock = ctx
    service = SchedulingService(session, clock=clock)
    run_at = clock.current + timedelta(hours=1)

    job = await service.schedule(user, job_input(run_at))

    assert job.user_id == user.id
    assert job.status == PENDING
    assert job.run_at.replace(tzinfo=timezone.utc) == run_at
    assert job.attempts == 0


@pytest.mark.asyncio
async def test_schedule_rejects_past_and_naive_times(ctx) -> None:
    session, user, _, clock = ctx
    service = SchedulingService(session, clock=clock)

    with pytest.raises(ScheduledTimeInPastError):
        await service.schedule(user, job_input(clock.current - timedelta(seconds=1)))

    with pytest.raises(ValueError):
        await service.schedule(user, job_input(datetime(2026, 8, 24, 13, 0)))


@pytest.mark.asyncio
async def test_idempotency_returns_same_job_and_detects_conflict(ctx) -> None:
    session, user, _, clock = ctx
    service = SchedulingService(session, clock=clock)
    run_at = clock.current + timedelta(minutes=5)

    first = await service.schedule(user, job_input(run_at, key="reminder:r-1"))
    second = await service.schedule(user, job_input(run_at, key="reminder:r-1"))
    assert second.id == first.id

    with pytest.raises(IdempotencyConflictError):
        await service.schedule(
            user,
            job_input(
                run_at + timedelta(minutes=1),
                key="reminder:r-1",
            ),
        )


@pytest.mark.asyncio
async def test_claim_due_leases_only_due_work(ctx) -> None:
    session, user, _, clock = ctx
    service = SchedulingService(session, clock=clock)
    due = await service.schedule(user, job_input(clock.current))
    future = await service.schedule(
        user,
        job_input(clock.current + timedelta(hours=1), payload={"id": "future"}),
    )

    claimed = await service.claim_due("worker-1")

    assert [job.id for job in claimed] == [due.id]
    assert claimed[0].status == RUNNING
    assert claimed[0].attempts == 1
    assert claimed[0].lease_owner == "worker-1"
    assert future.status == PENDING


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed(ctx) -> None:
    session, user, _, clock = ctx
    service = SchedulingService(session, clock=clock)
    job = await service.schedule(user, job_input(clock.current))
    await service.claim_due("worker-1", lease_duration=timedelta(seconds=10))
    clock.current += timedelta(seconds=11)

    claimed = await service.claim_due("worker-2")

    assert [item.id for item in claimed] == [job.id]
    assert job.lease_owner == "worker-2"
    assert job.attempts == 2


@pytest.mark.asyncio
async def test_complete_requires_current_worker_lease(ctx) -> None:
    session, user, _, clock = ctx
    service = SchedulingService(session, clock=clock)
    job = await service.schedule(user, job_input(clock.current))
    await service.claim_due("worker-1")

    with pytest.raises(WorkerLeaseError):
        await service.complete(job.id, "worker-2")

    completed = await service.complete(job.id, "worker-1")
    assert completed.status == COMPLETED
    assert completed.completed_at is not None
    assert completed.lease_owner is None


@pytest.mark.asyncio
async def test_failure_retries_then_becomes_terminal(ctx) -> None:
    session, user, _, clock = ctx
    service = SchedulingService(session, clock=clock)
    job = await service.schedule(
        user, job_input(clock.current, max_attempts=2)
    )
    await service.claim_due("worker-1")

    retried = await service.fail(
        job.id, "worker-1", "temporary", retry_delay=timedelta(seconds=5)
    )
    assert retried.status == PENDING
    assert retried.last_error == "temporary"

    clock.current += timedelta(seconds=5)
    await service.claim_due("worker-1")
    terminal = await service.fail(job.id, "worker-1", "permanent")
    assert terminal.status == FAILED
    assert terminal.attempts == 2


@pytest.mark.asyncio
async def test_cancel_enforces_ownership(ctx) -> None:
    session, user, other, clock = ctx
    service = SchedulingService(session, clock=clock)
    job = await service.schedule(user, job_input(clock.current + timedelta(hours=1)))

    with pytest.raises(ScheduledJobNotFoundError):
        await service.cancel(other, job.id)

    cancelled = await service.cancel(user, job.id)
    assert cancelled.status == "cancelled"


@pytest.mark.asyncio
async def test_runner_dispatches_and_completes_job(ctx) -> None:
    session, user, _, clock = ctx
    service = SchedulingService(session, clock=clock)
    job = await service.schedule(user, job_input(clock.current))
    handled: list[dict] = []

    async def handler(payload: dict) -> None:
        handled.append(payload)

    runner = ScheduledJobRunner(service, {"reminder.due": handler})
    processed = await runner.run_once("worker-1")

    assert [item.id for item in processed] == [job.id]
    assert handled == [{"reminder_id": "r-1"}]
    assert job.status == COMPLETED


@pytest.mark.asyncio
async def test_runner_records_unknown_handler_failure(ctx) -> None:
    session, user, _, clock = ctx
    service = SchedulingService(session, clock=clock)
    job = await service.schedule(user, job_input(clock.current, max_attempts=1))
    runner = ScheduledJobRunner(service, {})

    await runner.run_once("worker-1")

    assert job.status == FAILED
    assert "No handler registered" in (job.last_error or "")


@pytest.mark.asyncio
async def test_runner_rolls_back_partial_handler_work_before_retry(ctx) -> None:
    session, user, _, clock = ctx
    service = SchedulingService(session, clock=clock)
    job = await service.schedule(user, job_input(clock.current))
    original_email = user.email

    async def failing_handler(_payload: dict) -> None:
        user.email = "partial@example.com"
        await session.flush()
        raise RuntimeError("handler failed")

    runner = ScheduledJobRunner(service, {"reminder.due": failing_handler})
    await runner.run_once("worker-1")

    await session.refresh(user)
    await session.refresh(job)
    assert user.email == original_email
    assert job.status == PENDING
    assert job.last_error == "handler failed"


@pytest.mark.asyncio
async def test_worker_completion_does_not_overwrite_concurrent_cancellation(
    ctx,
) -> None:
    session, user, _, clock = ctx
    service = SchedulingService(session, clock=clock)
    job = await service.schedule(user, job_input(clock.current))
    claimed = await service.claim_due("worker-1")
    assert [item.id for item in claimed] == [job.id]

    job.status = "cancelled"
    job.lease_owner = None
    job.lease_expires_at = None
    await session.commit()

    settled = await service.complete(job.id, "worker-1")
    assert settled.status == "cancelled"
