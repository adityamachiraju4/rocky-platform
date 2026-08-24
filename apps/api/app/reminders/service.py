"""Authoritative Reminders lifecycle and scheduling integration."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.activity.recorder import ActivityRecorder
from app.core.time import UTC, Clock, ensure_utc, resolve_timezone, system_clock
from app.models.reminder import CANCELLED, COMPLETED, DUE, SCHEDULED, Reminder
from app.models.user import User
from app.reminders.exceptions import (
    InvalidReminderTransitionError,
    ReminderIdempotencyConflictError,
    ReminderNotDueError,
    ReminderNotFoundError,
)
from app.reminders.repository import ReminderRepository
from app.reminders.schemas import ReminderCreate
from app.scheduling.schemas import ScheduledJobCreate
from app.scheduling.service import SchedulingService

VALID_STATUSES = frozenset({SCHEDULED, DUE, COMPLETED, CANCELLED})


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return ensure_utc(value)


class RemindersService:
    def __init__(self, session: AsyncSession, *, clock: Clock = system_clock) -> None:
        self._session = session
        self._clock = clock
        self._reminders = ReminderRepository(session)
        self._scheduling = SchedulingService(session, clock=clock)
        self._activity = ActivityRecorder(session)

    def _now(self) -> datetime:
        return ensure_utc(self._clock.now())

    async def create_reminder(
        self, current_user: User, data: ReminderCreate
    ) -> Reminder:
        due_at = ensure_utc(data.due_at)
        timezone_name = resolve_timezone(data.timezone or current_user.timezone).key
        if due_at < self._now():
            from app.scheduling.exceptions import ScheduledTimeInPastError

            raise ScheduledTimeInPastError("Reminder due time is in the past.")

        if data.idempotency_key:
            existing = await self._reminders.get_by_idempotency_key(
                current_user.id, data.idempotency_key
            )
            if existing is not None:
                if not self._matches_request(existing, data, due_at, timezone_name):
                    raise ReminderIdempotencyConflictError(data.idempotency_key)
                return existing

        reminder = Reminder(
            user_id=current_user.id,
            title=data.title.strip(),
            notes=data.notes,
            due_at=due_at,
            timezone=timezone_name,
            idempotency_key=data.idempotency_key,
        )
        await self._reminders.add(reminder)
        job = await self._scheduling.schedule_in_transaction(
            current_user,
            ScheduledJobCreate(
                job_type="reminder.due",
                payload={"reminder_id": str(reminder.id)},
                run_at=due_at,
                idempotency_key=f"reminder.due:{reminder.id}",
            ),
        )
        reminder.scheduled_job_id = job.id
        await self._activity.record(
            user_id=current_user.id,
            event_type="reminder.created",
            entity_type="reminder",
            entity_id=reminder.id,
            payload={
                "title": reminder.title,
                "due_at": due_at.isoformat(),
                "timezone": timezone_name,
            },
        )
        await self._session.commit()
        await self._session.refresh(reminder)
        return reminder

    async def list_reminders(
        self, current_user: User, *, status: str | None = None
    ) -> list[Reminder]:
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(f"Unknown reminder status: {status}")
        return await self._reminders.list_owned(current_user.id, status)

    async def get_reminder(
        self, current_user: User, reminder_id: uuid.UUID
    ) -> Reminder:
        reminder = await self._reminders.get_owned(current_user.id, reminder_id)
        if reminder is None:
            raise ReminderNotFoundError(str(reminder_id))
        return reminder

    async def complete_reminder(
        self, current_user: User, reminder_id: uuid.UUID
    ) -> Reminder:
        reminder = await self._reminders.get_owned_for_update(
            current_user.id, reminder_id
        )
        if reminder is None:
            raise ReminderNotFoundError(str(reminder_id))
        if reminder.status not in {SCHEDULED, DUE}:
            raise InvalidReminderTransitionError(reminder.status)
        reminder.status = COMPLETED
        reminder.completed_at = self._now()
        if reminder.scheduled_job_id is not None:
            await self._cancel_job_if_open(current_user, reminder.scheduled_job_id)
        await self._activity.record(
            user_id=current_user.id,
            event_type="reminder.completed",
            entity_type="reminder",
            entity_id=reminder.id,
            payload={"title": reminder.title},
        )
        await self._session.commit()
        await self._session.refresh(reminder)
        return reminder

    async def cancel_reminder(
        self, current_user: User, reminder_id: uuid.UUID
    ) -> Reminder:
        reminder = await self._reminders.get_owned_for_update(
            current_user.id, reminder_id
        )
        if reminder is None:
            raise ReminderNotFoundError(str(reminder_id))
        if reminder.status not in {SCHEDULED, DUE}:
            raise InvalidReminderTransitionError(reminder.status)
        reminder.status = CANCELLED
        if reminder.scheduled_job_id is not None:
            await self._cancel_job_if_open(current_user, reminder.scheduled_job_id)
        await self._activity.record(
            user_id=current_user.id,
            event_type="reminder.cancelled",
            entity_type="reminder",
            entity_id=reminder.id,
            payload={"title": reminder.title},
        )
        await self._session.commit()
        await self._session.refresh(reminder)
        return reminder

    async def mark_due(self, reminder_id: uuid.UUID) -> Reminder:
        reminder = await self._reminders.get_for_update(reminder_id)
        if reminder is None:
            raise ReminderNotFoundError(str(reminder_id))
        if reminder.status in {DUE, COMPLETED, CANCELLED}:
            return reminder
        if _stored_utc(reminder.due_at) > self._now():
            raise ReminderNotDueError(str(reminder_id))
        reminder.status = DUE
        reminder.triggered_at = self._now()
        await self._activity.record(
            user_id=reminder.user_id,
            event_type="reminder.due",
            entity_type="reminder",
            entity_id=reminder.id,
            payload={"title": reminder.title},
        )
        await self._session.commit()
        await self._session.refresh(reminder)
        return reminder

    async def _cancel_job_if_open(
        self, current_user: User, job_id: uuid.UUID
    ) -> None:
        from app.scheduling.exceptions import InvalidJobTransitionError

        try:
            await self._scheduling.cancel_in_transaction(current_user, job_id)
        except InvalidJobTransitionError:
            pass

    @staticmethod
    def _matches_request(
        reminder: Reminder,
        data: ReminderCreate,
        due_at: datetime,
        timezone_name: str,
    ) -> bool:
        return (
            reminder.title == data.title.strip()
            and reminder.notes == data.notes
            and _stored_utc(reminder.due_at) == due_at
            and reminder.timezone == timezone_name
        )
