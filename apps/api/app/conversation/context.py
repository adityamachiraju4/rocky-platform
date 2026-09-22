"""Database-backed, bounded conversation threads and grounded references."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import (
    ConversationThread,
    ConversationTurnRecord,
    GroundedReference,
)

MAX_STORED_TURNS = 100
RECENT_TURN_LIMIT = 12
RECENT_CHARACTER_BUDGET = 6000

ReferenceKind = Literal[
    "project", "task", "reminder", "note", "list", "notification",
    "live_subject", "place", "prior_result",
]


class ConversationThreadNotFoundError(LookupError):
    """The requested thread is absent or not owned by the current user."""


@dataclass(frozen=True)
class DurableReference:
    kind: str
    entity_id: uuid.UUID | None
    display_text: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RecentTurn:
    role: str
    content: str
    language: str | None


class ConversationContextStore:
    """Persistence facade scoped by both authenticated user and thread."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve_thread(
        self, user_id: uuid.UUID, thread_id: uuid.UUID | None
    ) -> ConversationThread:
        if thread_id is not None:
            thread = await self._session.scalar(
                select(ConversationThread).where(
                    ConversationThread.id == thread_id,
                    ConversationThread.user_id == user_id,
                )
            )
            if thread is None:
                raise ConversationThreadNotFoundError(str(thread_id))
            return thread

        thread = await self._session.scalar(
            select(ConversationThread).where(
                ConversationThread.user_id == user_id,
                ConversationThread.default_key == "default",
            )
        )
        if thread is not None:
            return thread

        thread = ConversationThread(user_id=user_id, default_key="default")
        self._session.add(thread)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            thread = await self._session.scalar(
                select(ConversationThread).where(
                    ConversationThread.user_id == user_id,
                    ConversationThread.default_key == "default",
                )
            )
            if thread is None:  # pragma: no cover - defensive database guard
                raise
            return thread
        await self._session.refresh(thread)
        return thread

    async def create_thread(
        self, user_id: uuid.UUID, *, title: str | None = None
    ) -> ConversationThread:
        thread = ConversationThread(
            user_id=user_id, title=title.strip() if title else None
        )
        self._session.add(thread)
        await self._session.commit()
        await self._session.refresh(thread)
        return thread

    async def add_turn(
        self,
        thread: ConversationThread,
        *,
        role: Literal["user", "assistant"],
        content: str,
        language: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationTurnRecord:
        turn = ConversationTurnRecord(
            thread_id=thread.id,
            role=role,
            content=content[:4000],
            language=language,
            turn_metadata=metadata or {},
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(turn)
        thread.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        stale_ids = list(
            (
                await self._session.scalars(
                    select(ConversationTurnRecord.id)
                    .where(ConversationTurnRecord.thread_id == thread.id)
                    .order_by(
                        ConversationTurnRecord.created_at.desc(),
                        ConversationTurnRecord.id.desc(),
                    )
                    .offset(MAX_STORED_TURNS)
                )
            ).all()
        )
        if stale_ids:
            await self._session.execute(
                delete(ConversationTurnRecord).where(
                    ConversationTurnRecord.id.in_(stale_ids)
                )
            )
        await self._session.commit()
        await self._session.refresh(turn)
        return turn

    async def recent_turns(self, thread_id: uuid.UUID) -> tuple[RecentTurn, ...]:
        rows = list(
            (
                await self._session.scalars(
                    select(ConversationTurnRecord)
                    .where(ConversationTurnRecord.thread_id == thread_id)
                    .order_by(
                        ConversationTurnRecord.created_at.desc(),
                        ConversationTurnRecord.id.desc(),
                    )
                    .limit(RECENT_TURN_LIMIT)
                )
            ).all()
        )
        selected: list[ConversationTurnRecord] = []
        used = 0
        for row in rows:
            remaining = RECENT_CHARACTER_BUDGET - used
            if remaining <= 0 or (len(row.content) > remaining and selected):
                break
            selected.append(row)
            used += min(len(row.content), remaining)
        return tuple(
            RecentTurn(row.role, row.content, row.language)
            for row in reversed(selected)
        )

    async def references(
        self, thread_id: uuid.UUID
    ) -> dict[str, DurableReference]:
        rows = (
            await self._session.scalars(
                select(GroundedReference).where(
                    GroundedReference.thread_id == thread_id
                )
            )
        ).all()
        return {
            row.kind: DurableReference(
                row.kind,
                row.entity_id,
                row.display_text,
                dict(row.reference_metadata or {}),
            )
            for row in rows
        }

    async def set_reference(
        self,
        thread_id: uuid.UUID,
        *,
        kind: ReferenceKind,
        entity_id: uuid.UUID | None,
        display_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        row = await self._session.scalar(
            select(GroundedReference).where(
                GroundedReference.thread_id == thread_id,
                GroundedReference.kind == kind,
            )
        )
        if row is None:
            row = GroundedReference(thread_id=thread_id, kind=kind)
            self._session.add(row)
        row.entity_id = entity_id
        row.display_text = display_text[:500]
        row.reference_metadata = metadata or {}
        try:
            await self._session.commit()
        except IntegrityError:
            # Concurrent turns may create the same per-thread reference kind.
            await self._session.rollback()
            row = await self._session.scalar(
                select(GroundedReference).where(
                    GroundedReference.thread_id == thread_id,
                    GroundedReference.kind == kind,
                )
            )
            if row is None:  # pragma: no cover - defensive database guard
                raise
            row.entity_id = entity_id
            row.display_text = display_text[:500]
            row.reference_metadata = metadata or {}
            await self._session.commit()
