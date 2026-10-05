"""Ownership is enforced in every memory lookup predicate."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import Memory


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, memory: Memory) -> Memory:
        self._session.add(memory)
        await self._session.flush()
        return memory

    async def get_owned(self, user_id: uuid.UUID, memory_id: uuid.UUID) -> Memory | None:
        return await self._session.scalar(select(Memory).where(
            Memory.id == memory_id, Memory.user_id == user_id,
        ))

    async def get_owned_for_update(
        self, user_id: uuid.UUID, memory_id: uuid.UUID
    ) -> Memory | None:
        result = await self._session.execute(
            select(Memory)
            .where(Memory.id == memory_id, Memory.user_id == user_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_owned(self, user_id: uuid.UUID, status: str) -> list[Memory]:
        result = await self._session.scalars(select(Memory).where(
            Memory.user_id == user_id, Memory.status == status,
        ).order_by(Memory.updated_at.desc(), Memory.created_at.desc(), Memory.id.desc()))
        return list(result.all())
