"""Authoritative Lists ownership, ordering, and lifecycle policy."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.activity.recorder import ActivityRecorder
from app.core.time import Clock, ensure_utc, system_clock
from app.lists.exceptions import (
    InvalidListItemTransitionError,
    InvalidListTransitionError,
    ListItemNotFoundError,
    ListNotFoundError,
)
from app.lists.repository import ListRepository
from app.lists.schemas import ListCreate, ListItemCreate, ListItemUpdate, ListUpdate
from app.models.list import ACTIVE, ARCHIVED, COMPLETE, List, ListItem
from app.models.user import User


class ListsService:
    def __init__(self, session: AsyncSession, *, clock: Clock = system_clock) -> None:
        self._session = session
        self._clock = clock
        self._lists = ListRepository(session)
        self._activity = ActivityRecorder(session)

    def _now(self):
        return ensure_utc(self._clock.now())

    async def create_list(self, user: User, data: ListCreate) -> List:
        value = await self._lists.add_list(List(user_id=user.id, title=data.title.strip()))
        await self._activity.record(
            user_id=user.id, event_type="list.created", entity_type="list",
            entity_id=value.id, payload={"title": value.title},
        )
        await self._session.commit()
        await self._session.refresh(value)
        return value

    async def list_lists(self, user: User, *, status: str = ACTIVE) -> list[List]:
        if status not in {ACTIVE, ARCHIVED}:
            raise ValueError(f"Unknown list status: {status}")
        return await self._lists.list_owned(user.id, status)

    async def get_list(self, user: User, list_id: uuid.UUID) -> List:
        value = await self._lists.get_owned_list(user.id, list_id)
        if value is None:
            raise ListNotFoundError(str(list_id))
        return value

    async def update_list(
        self, user: User, list_id: uuid.UUID, data: ListUpdate
    ) -> List:
        value = await self.get_list(user, list_id)
        updates = data.model_dump(exclude_unset=True)
        archive = updates.pop("status", None) == ARCHIVED
        if value.status == ARCHIVED:
            if archive and not updates:
                return value
            raise InvalidListTransitionError(value.status)
        if "title" in updates and updates["title"] != value.title:
            value.title = updates["title"].strip()
        if archive:
            value.status = ARCHIVED
            value.archived_at = self._now()
            await self._activity.record(
                user_id=user.id, event_type="list.archived", entity_type="list",
                entity_id=value.id, payload={"title": value.title},
            )
        await self._session.commit()
        await self._session.refresh(value)
        return value

    async def create_item(
        self, user: User, list_id: uuid.UUID, data: ListItemCreate
    ) -> ListItem:
        parent = await self._lists.get_owned_list(user.id, list_id, for_update=True)
        if parent is None:
            raise ListNotFoundError(str(list_id))
        if parent.status == ARCHIVED:
            raise InvalidListTransitionError(parent.status)
        item = await self._lists.add_item(
            ListItem(
                list_id=list_id,
                content=data.content.strip(),
                position=await self._lists.next_position(list_id),
            )
        )
        parent.updated_at = self._now()
        await self._activity.record(
            user_id=user.id, event_type="list.item_added", entity_type="list_item",
            entity_id=item.id, payload={"list_title": parent.title, "content": item.content},
        )
        await self._session.commit()
        await self._session.refresh(item)
        return item

    async def list_items(
        self, user: User, list_id: uuid.UUID, *, status: str | None = None
    ) -> list[ListItem]:
        await self.get_list(user, list_id)
        if status not in {None, ACTIVE, COMPLETE}:
            raise ValueError(f"Unknown list item status: {status}")
        return await self._lists.list_items(user.id, list_id, status)

    async def get_item(
        self, user: User, list_id: uuid.UUID, item_id: uuid.UUID
    ) -> ListItem:
        await self.get_list(user, list_id)
        item = await self._lists.get_owned_item(user.id, list_id, item_id)
        if item is None:
            raise ListItemNotFoundError(str(item_id))
        return item

    async def update_item(
        self, user: User, list_id: uuid.UUID, item_id: uuid.UUID,
        data: ListItemUpdate,
    ) -> ListItem:
        parent = await self.get_list(user, list_id)
        if parent.status == ARCHIVED:
            raise InvalidListTransitionError(parent.status)
        item = await self.get_item(user, list_id, item_id)
        updates = data.model_dump(exclude_unset=True)
        complete = updates.pop("status", None) == COMPLETE
        if item.status == COMPLETE:
            if complete and not updates:
                return item
            raise InvalidListItemTransitionError(item.status)
        if "content" in updates and updates["content"] != item.content:
            item.content = updates["content"].strip()
        if complete:
            item.status = COMPLETE
            item.completed_at = self._now()
            await self._activity.record(
                user_id=user.id, event_type="list.item_completed",
                entity_type="list_item", entity_id=item.id,
                payload={"list_title": parent.title, "content": item.content},
            )
        parent.updated_at = self._now()
        await self._session.commit()
        await self._session.refresh(item)
        return item
