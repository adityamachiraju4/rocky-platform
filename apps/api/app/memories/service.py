"""Authoritative owned explicit memory and its irreversible lifecycle."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.activity.recorder import ActivityRecorder, EventType
from app.core.time import Clock, ensure_utc, system_clock
from app.memories.exceptions import InvalidMemoryTransitionError, MemoryNotFoundError
from app.memories.repository import MemoryRepository
from app.memories.schemas import MemoryCreate, MemoryUpdate
from app.models.conversation import ConversationThread
from app.models.memory import ACTIVE, EXPLICIT_USER, FORGOTTEN, Memory
from app.models.user import User


class MemoriesService:
    def __init__(self, session: AsyncSession, *, clock: Clock = system_clock) -> None:
        self._session = session
        self._clock = clock
        self._memories = MemoryRepository(session)
        self._activity = ActivityRecorder(session)

    async def remember(self, user: User, data: MemoryCreate, *, source_thread_id: uuid.UUID | None = None) -> Memory:
        if source_thread_id is not None:
            owned_thread = await self._session.scalar(select(ConversationThread.id).where(
                ConversationThread.id == source_thread_id, ConversationThread.user_id == user.id,
            ))
            if owned_thread is None:
                raise MemoryNotFoundError("Memory provenance thread not found")
        now = ensure_utc(self._clock.now())
        memory = Memory(user_id=user.id, kind=data.kind, subject=data.subject, content=data.content,
                        status=ACTIVE, source=EXPLICIT_USER, source_thread_id=source_thread_id,
                        created_at=now, updated_at=now)
        await self._memories.add(memory)
        await self._record(user, memory, "memory.remembered")
        await self._session.commit()
        await self._session.refresh(memory)
        return memory

    async def list_memories(self, user: User, *, status: str = ACTIVE) -> list[Memory]:
        if status not in {ACTIVE, FORGOTTEN}:
            raise ValueError("Unknown memory status")
        return await self._memories.list_owned(user.id, status)

    async def get_memory(self, user: User, memory_id: uuid.UUID) -> Memory:
        memory = await self._memories.get_owned(user.id, memory_id)
        if memory is None:
            raise MemoryNotFoundError("Memory not found")
        return memory

    async def update_memory(self, user: User, memory_id: uuid.UUID, data: MemoryUpdate) -> Memory:
        memory = await self._memories.get_owned_for_update(user.id, memory_id)
        if memory is None:
            raise MemoryNotFoundError("Memory not found")
        updates = data.model_dump(exclude_unset=True)
        forget = updates.pop("status", None) == FORGOTTEN
        if memory.status == FORGOTTEN:
            if forget and not updates:
                return memory
            raise InvalidMemoryTransitionError("Forgotten memory cannot be updated")
        changed = [field for field, value in updates.items() if getattr(memory, field) != value]
        for field in changed:
            setattr(memory, field, updates[field])
        if forget:
            memory.status = FORGOTTEN
            memory.forgotten_at = ensure_utc(self._clock.now())
        if changed or forget:
            memory.updated_at = ensure_utc(self._clock.now())
            await self._record(user, memory, "memory.forgotten" if forget else "memory.updated")
            await self._session.commit()
            await self._session.refresh(memory)
        return memory

    async def forget(self, user: User, memory_id: uuid.UUID) -> Memory:
        return await self.update_memory(user, memory_id, MemoryUpdate(status=FORGOTTEN))

    async def _record(self, user: User, memory: Memory, event_type: EventType) -> None:
        await self._activity.record(user_id=user.id, event_type=event_type, entity_type="memory",
                                    entity_id=memory.id, payload={"kind": memory.kind, "subject": memory.subject})
