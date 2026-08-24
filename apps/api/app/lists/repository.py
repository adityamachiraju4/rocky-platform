"""Ownership-scoped persistence for Lists and ListItems."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.list import List, ListItem


class ListRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_list(self, value: List) -> List:
        self._session.add(value)
        await self._session.flush()
        return value

    async def get_owned_list(
        self, user_id: uuid.UUID, list_id: uuid.UUID, *, for_update: bool = False
    ) -> List | None:
        statement = select(List).where(List.id == list_id, List.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_owned(self, user_id: uuid.UUID, status: str) -> list[List]:
        result = await self._session.execute(
            select(List)
            .where(List.user_id == user_id, List.status == status)
            .order_by(List.updated_at.desc(), List.created_at.desc(), List.id.desc())
        )
        return list(result.scalars().all())

    async def add_item(self, item: ListItem) -> ListItem:
        self._session.add(item)
        await self._session.flush()
        return item

    async def next_position(self, list_id: uuid.UUID) -> int:
        value = await self._session.scalar(
            select(func.max(ListItem.position)).where(ListItem.list_id == list_id)
        )
        return 0 if value is None else value + 1

    async def get_owned_item(
        self, user_id: uuid.UUID, list_id: uuid.UUID, item_id: uuid.UUID
    ) -> ListItem | None:
        result = await self._session.execute(
            select(ListItem)
            .join(List, ListItem.list_id == List.id)
            .where(
                ListItem.id == item_id,
                ListItem.list_id == list_id,
                List.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_items(
        self, user_id: uuid.UUID, list_id: uuid.UUID, status: str | None
    ) -> list[ListItem]:
        statement = (
            select(ListItem)
            .join(List, ListItem.list_id == List.id)
            .where(ListItem.list_id == list_id, List.user_id == user_id)
        )
        if status is not None:
            statement = statement.where(ListItem.status == status)
        result = await self._session.execute(statement.order_by(ListItem.position))
        return list(result.scalars().all())
