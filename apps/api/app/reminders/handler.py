"""Scheduled handler that moves a persisted reminder into due state."""
from __future__ import annotations

import uuid
from typing import Any

from app.reminders.exceptions import InvalidReminderPayloadError
from app.reminders.service import RemindersService
from app.models.reminder import DUE
from app.notifications.schemas import NotificationCreate
from app.notifications.service import NotificationsService


class ReminderDueHandler:
    def __init__(
        self,
        reminders: RemindersService,
        notifications: NotificationsService,
    ) -> None:
        self._reminders = reminders
        self._notifications = notifications

    async def __call__(self, payload: dict[str, Any]) -> None:
        raw_id = payload.get("reminder_id")
        try:
            reminder_id = uuid.UUID(str(raw_id))
        except (TypeError, ValueError) as exc:
            raise InvalidReminderPayloadError("Missing reminder_id.") from exc
        reminder = await self._reminders.mark_due(reminder_id)
        if reminder.status != DUE:
            return
        await self._notifications.create_for_user(
            reminder.user_id,
            NotificationCreate(
                type="reminder",
                title=reminder.title,
                body=reminder.notes or reminder.title,
                source_type="reminder",
                source_id=reminder.id,
                source_metadata={"due_at": reminder.due_at.isoformat()},
            ),
        )
