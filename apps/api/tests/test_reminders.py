from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.dependencies import get_session
from app.db.base import Base
from app.main import app as fastapi_app
from app.models.activity import Activity
from app.models.reminder import Reminder
from app.models.notification import Notification
from app.models.scheduled_job import CANCELLED, COMPLETED, ScheduledJob
from app.models.user import User
from app.reminders.exceptions import InvalidReminderPayloadError
from app.reminders.handler import ReminderDueHandler
from app.reminders.schemas import ReminderCreate
from app.reminders.service import RemindersService
from app.notifications.service import NotificationsService
from app.scheduling.runner import ScheduledJobRunner
from app.scheduling.service import SchedulingService
import app.models  # noqa: F401

SECRET = "test-secret-key"
PEPPER = "test-refresh-pepper"
PASSWORD = "correct horse battery"


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    monkeypatch.setenv("REFRESH_TOKEN_PEPPER", PEPPER)


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    fastapi_app.dependency_overrides[get_session] = override_session
    try:
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client, maker
    finally:
        fastapi_app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


async def auth_headers(
    client: httpx.AsyncClient,
    maker: async_sessionmaker,
    *,
    timezone_name: str = "UTC",
) -> tuple[dict[str, str], uuid.UUID]:
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    response = await client.post(
        "/identity/users",
        json={
            "email": email,
            "password": PASSWORD,
            "full_name": "Ada",
            "timezone": timezone_name,
        },
    )
    assert response.status_code == 201, response.text
    user_id = uuid.UUID(response.json()["id"])
    async with maker() as session:
        await session.execute(
            update(User).where(User.id == user_id).values(is_verified=True)
        )
        await session.commit()
    login = await client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    return {
        "Authorization": f"Bearer {login.json()['access_token']}"
    }, user_id


def future_due(minutes: int = 10) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


@pytest.mark.asyncio
async def test_reminders_require_auth(ctx) -> None:
    client, _ = ctx
    assert (await client.get("/reminders")).status_code == 401
    response = await client.post(
        "/reminders", json={"title": "Call Ramesh", "due_at": future_due()}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_persists_reminder_job_and_activity_atomically(ctx) -> None:
    client, maker = ctx
    headers, user_id = await auth_headers(
        client, maker, timezone_name="Asia/Kolkata"
    )
    response = await client.post(
        "/reminders",
        json={"title": "Call Ramesh", "due_at": future_due()},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "scheduled"
    assert body["timezone"] == "Asia/Kolkata"

    async with maker() as session:
        reminder = await session.get(Reminder, uuid.UUID(body["id"]))
        assert reminder is not None and reminder.scheduled_job_id is not None
        job = await session.get(ScheduledJob, reminder.scheduled_job_id)
        assert job is not None
        assert job.job_type == "reminder.due"
        assert job.user_id == user_id
        activity = (
            await session.execute(
                select(Activity).where(
                    Activity.entity_id == reminder.id,
                    Activity.event_type == "reminder.created",
                )
            )
        ).scalar_one()
        assert activity.payload["title"] == "Call Ramesh"


@pytest.mark.asyncio
async def test_create_rejects_past_naive_and_invalid_timezone(ctx) -> None:
    client, maker = ctx
    headers, _ = await auth_headers(client, maker)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    response = await client.post(
        "/reminders",
        json={"title": "Past", "due_at": past.isoformat()},
        headers=headers,
    )
    assert response.status_code == 422

    response = await client.post(
        "/reminders",
        json={"title": "Naive", "due_at": "2026-08-24T18:00:00"},
        headers=headers,
    )
    assert response.status_code == 422

    response = await client.post(
        "/reminders",
        json={
            "title": "Wrong zone",
            "due_at": future_due(),
            "timezone": "Mars/Olympus",
        },
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_and_get_are_owner_scoped(ctx) -> None:
    client, maker = ctx
    mine, _ = await auth_headers(client, maker)
    theirs, _ = await auth_headers(client, maker)
    created = await client.post(
        "/reminders",
        json={"title": "Mine", "due_at": future_due()},
        headers=mine,
    )
    reminder_id = created.json()["id"]

    listed = await client.get("/reminders?status=scheduled", headers=mine)
    assert listed.status_code == 200
    assert [item["title"] for item in listed.json()] == ["Mine"]
    assert (await client.get(f"/reminders/{reminder_id}", headers=mine)).status_code == 200
    assert (await client.get(f"/reminders/{reminder_id}", headers=theirs)).status_code == 404


@pytest.mark.asyncio
async def test_idempotency_is_user_scoped(ctx) -> None:
    client, maker = ctx
    headers, _ = await auth_headers(client, maker)
    payload = {
        "title": "Once",
        "due_at": future_due(),
        "idempotency_key": "request-1",
    }
    first = await client.post("/reminders", json=payload, headers=headers)
    second = await client.post("/reminders", json=payload, headers=headers)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    conflict = await client.post(
        "/reminders",
        json={**payload, "title": "Different"},
        headers=headers,
    )
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_complete_cancels_open_job_and_records_activity(ctx) -> None:
    client, maker = ctx
    headers, _ = await auth_headers(client, maker)
    created = await client.post(
        "/reminders",
        json={"title": "Finish", "due_at": future_due()},
        headers=headers,
    )
    reminder_id = uuid.UUID(created.json()["id"])
    response = await client.post(
        f"/reminders/{reminder_id}/complete", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["completed_at"] is not None

    async with maker() as session:
        reminder = await session.get(Reminder, reminder_id)
        job = await session.get(ScheduledJob, reminder.scheduled_job_id)
        assert job.status == CANCELLED
        count = await session.scalar(
            select(func.count(Activity.id)).where(
                Activity.entity_id == reminder_id,
                Activity.event_type == "reminder.completed",
            )
        )
        assert count == 1


@pytest.mark.asyncio
async def test_cancel_and_repeat_transition(ctx) -> None:
    client, maker = ctx
    headers, _ = await auth_headers(client, maker)
    created = await client.post(
        "/reminders",
        json={"title": "Cancel", "due_at": future_due()},
        headers=headers,
    )
    reminder_id = created.json()["id"]
    response = await client.delete(f"/reminders/{reminder_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert (
        await client.post(f"/reminders/{reminder_id}/complete", headers=headers)
    ).status_code == 409


@pytest.mark.asyncio
async def test_due_handler_is_idempotent(ctx) -> None:
    _, maker = ctx
    clock = MutableClock(datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc))
    async with maker() as session:
        user = User(
            email="due@example.com",
            password_hash="hash",
            timezone="UTC",
            is_verified=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        service = RemindersService(session, clock=clock)
        reminder = await service.create_reminder(
            user,
            ReminderCreate(
                title="Due now",
                due_at=clock.current + timedelta(minutes=1),
            ),
        )
        handler = ReminderDueHandler(service, NotificationsService(session, clock=clock))
        clock.current += timedelta(minutes=1)

        await handler({"reminder_id": str(reminder.id)})
        await handler({"reminder_id": str(reminder.id)})

        assert reminder.status == "due"
        assert reminder.triggered_at is not None
        count = await session.scalar(
            select(func.count(Activity.id)).where(
                Activity.entity_id == reminder.id,
                Activity.event_type == "reminder.due",
            )
        )
        assert count == 1
        notifications = list(
            (
                await session.execute(
                    select(Notification).where(
                        Notification.source_id == reminder.id
                    )
                )
            ).scalars()
        )
        assert len(notifications) == 1
        assert notifications[0].user_id == user.id
        assert notifications[0].status == "unread"
        assert reminder.status == "due"


@pytest.mark.asyncio
async def test_due_handler_rejects_invalid_payload(ctx) -> None:
    _, maker = ctx
    async with maker() as session:
        handler = ReminderDueHandler(
            RemindersService(session), NotificationsService(session)
        )
        with pytest.raises(InvalidReminderPayloadError):
            await handler({"reminder_id": "not-a-uuid"})


@pytest.mark.asyncio
async def test_worker_retries_notification_failure_without_duplicates(ctx) -> None:
    _, maker = ctx
    clock = MutableClock(datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc))

    class FlakyNotificationsService(NotificationsService):
        attempts = 0

        async def create_for_user(self, user_id, data):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("delivery unavailable")
            return await super().create_for_user(user_id, data)

    async with maker() as session:
        user = User(
            email="retry@example.com",
            password_hash="hash",
            timezone="UTC",
            is_verified=True,
        )
        session.add(user)
        await session.commit()
        reminder = await RemindersService(session, clock=clock).create_reminder(
            user,
            ReminderCreate(
                title="Retry delivery",
                due_at=clock.current + timedelta(minutes=1),
            ),
        )
        scheduling = SchedulingService(session, clock=clock)
        handler = ReminderDueHandler(
            RemindersService(session, clock=clock),
            FlakyNotificationsService(session, clock=clock),
        )
        runner = ScheduledJobRunner(scheduling, {"reminder.due": handler})
        clock.current += timedelta(minutes=1)

        await runner.run_once("worker-1")
        await session.refresh(reminder)
        job = await session.get(ScheduledJob, reminder.scheduled_job_id)
        assert reminder.status == "due"
        assert job.status == "pending"
        assert "delivery unavailable" in job.last_error

        clock.current += timedelta(seconds=30)
        await runner.run_once("worker-1")
        await session.refresh(job)
        assert job.status == COMPLETED
        notifications = list(
            (
                await session.execute(
                    select(Notification).where(
                        Notification.source_id == reminder.id
                    )
                )
            ).scalars()
        )
        assert len(notifications) == 1
        assert reminder.status == "due"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_action", ["complete", "cancel"])
async def test_terminal_reminder_never_delivers_notification(
    ctx, terminal_action: str
) -> None:
    _, maker = ctx
    clock = MutableClock(datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc))
    async with maker() as session:
        user = User(
            email=f"terminal-{terminal_action}@example.com",
            password_hash="hash",
            timezone="UTC",
            is_verified=True,
        )
        session.add(user)
        await session.commit()
        service = RemindersService(session, clock=clock)
        reminder = await service.create_reminder(
            user,
            ReminderCreate(
                title="Do not deliver",
                due_at=clock.current + timedelta(minutes=1),
            ),
        )
        if terminal_action == "complete":
            await service.complete_reminder(user, reminder.id)
        else:
            await service.cancel_reminder(user, reminder.id)
        clock.current += timedelta(minutes=1)

        handler = ReminderDueHandler(
            service, NotificationsService(session, clock=clock)
        )
        await handler({"reminder_id": str(reminder.id)})

        notifications = list(
            (
                await session.execute(
                    select(Notification).where(
                        Notification.source_id == reminder.id
                    )
                )
            ).scalars()
        )
        assert notifications == []
        assert reminder.status == (
            "completed" if terminal_action == "complete" else "cancelled"
        )
