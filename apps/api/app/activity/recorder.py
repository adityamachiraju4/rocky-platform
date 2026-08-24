"""Deliberate emission primitive for the Activity capability.

The recorder is a *persistence helper*, structurally a repository: it takes an
:class:`AsyncSession`, does ``add`` + ``flush``, and never commits. Domain
services (Projects, Tasks) construct it with their own session and call
:meth:`record` before their existing ``commit`` — so the activity row commits
atomically with the mutation it records, in the one transaction the service
already owns. There is no dual-write gap and no second transaction.

``EventType`` is a closed set enforced here at the Python boundary, not by a
database constraint. Event types are internal — emitted only by our own
services, never client-supplied — so a DB enum would add migration friction
with no safety payoff. The ``Literal`` gives typo-safety and autocomplete at
every call site (the real risk surface) while leaving the column open for the
downstream Context / Memory / AI layers to read.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity

EventType = Literal[
    "project.created",
    "project.updated",
    "task.created",
    "task.updated",
    "task.completed",
    "reminder.created",
    "reminder.due",
    "reminder.completed",
    "reminder.cancelled",
    "note.created",
    "note.updated",
    "note.archived",
    "list.created",
    "list.archived",
    "list.item_added",
    "list.item_completed",
]


class ActivityRecorder:
    """Records a domain event within the caller's transaction (no commit)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        user_id: uuid.UUID,
        event_type: EventType,
        entity_type: str,
        entity_id: uuid.UUID,
        payload: dict[str, Any] | None = None,
    ) -> Activity:
        activity = Activity(
            user_id=user_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload or {},
        )
        self._session.add(activity)
        await self._session.flush()
        return activity
