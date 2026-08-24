"""Ownership-scoped persistence for Notes."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.note import Note


class NoteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, note: Note) -> Note:
        self._session.add(note)
        await self._session.flush()
        return note

    async def get_owned(
        self, user_id: uuid.UUID, note_id: uuid.UUID
    ) -> Note | None:
        result = await self._session.execute(
            select(Note).where(Note.id == note_id, Note.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_owned(self, user_id: uuid.UUID, status: str) -> list[Note]:
        result = await self._session.execute(
            select(Note)
            .where(Note.user_id == user_id, Note.status == status)
            .order_by(Note.updated_at.desc(), Note.created_at.desc(), Note.id.desc())
        )
        return list(result.scalars().all())
