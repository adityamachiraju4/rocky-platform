"""Authoritative Notes ownership, mutation, and lifecycle policy."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.activity.recorder import ActivityRecorder
from app.core.time import Clock, ensure_utc, system_clock
from app.models.note import ACTIVE, ARCHIVED, Note
from app.models.user import User
from app.notes.exceptions import InvalidNoteTransitionError, NoteNotFoundError
from app.notes.repository import NoteRepository
from app.notes.schemas import NoteCreate, NoteUpdate


class NotesService:
    def __init__(self, session: AsyncSession, *, clock: Clock = system_clock) -> None:
        self._session = session
        self._clock = clock
        self._notes = NoteRepository(session)
        self._activity = ActivityRecorder(session)

    async def create_note(self, current_user: User, data: NoteCreate) -> Note:
        note = Note(
            user_id=current_user.id,
            title=data.title.strip(),
            content=data.content,
        )
        await self._notes.add(note)
        await self._activity.record(
            user_id=current_user.id,
            event_type="note.created",
            entity_type="note",
            entity_id=note.id,
            payload={"title": note.title},
        )
        await self._session.commit()
        await self._session.refresh(note)
        return note

    async def list_notes(
        self, current_user: User, *, status: str = ACTIVE
    ) -> list[Note]:
        if status not in {ACTIVE, ARCHIVED}:
            raise ValueError(f"Unknown note status: {status}")
        return await self._notes.list_owned(current_user.id, status)

    async def get_note(self, current_user: User, note_id: uuid.UUID) -> Note:
        note = await self._notes.get_owned(current_user.id, note_id)
        if note is None:
            raise NoteNotFoundError(str(note_id))
        return note

    async def update_note(
        self, current_user: User, note_id: uuid.UUID, data: NoteUpdate
    ) -> Note:
        note = await self.get_note(current_user, note_id)
        updates = data.model_dump(exclude_unset=True)
        archive_requested = updates.pop("status", None) == ARCHIVED

        if note.status == ARCHIVED:
            if archive_requested and not updates:
                return note
            raise InvalidNoteTransitionError(note.status)

        changed_fields = [
            field
            for field, value in updates.items()
            if getattr(note, field) != value
        ]
        for field in changed_fields:
            setattr(note, field, updates[field])

        if archive_requested:
            note.status = ARCHIVED
            note.archived_at = ensure_utc(self._clock.now())
            await self._activity.record(
                user_id=current_user.id,
                event_type="note.archived",
                entity_type="note",
                entity_id=note.id,
                payload={"title": note.title},
            )
        elif changed_fields:
            await self._activity.record(
                user_id=current_user.id,
                event_type="note.updated",
                entity_type="note",
                entity_id=note.id,
                payload={"title": note.title, "changed_fields": changed_fields},
            )

        await self._session.commit()
        await self._session.refresh(note)
        return note
