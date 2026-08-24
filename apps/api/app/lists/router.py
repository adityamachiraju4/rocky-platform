"""Authenticated HTTP surface for Lists and ordered items."""
from __future__ import annotations

import uuid
from fastapi import APIRouter, HTTPException, Query, status

from app.auth.dependencies import CurrentUserDep
from app.lists.dependencies import ListsServiceDep
from app.lists.exceptions import (
    InvalidListItemTransitionError, InvalidListTransitionError,
    ListItemNotFoundError, ListNotFoundError,
)
from app.lists.schemas import (
    ListCreate, ListItemCreate, ListItemRead, ListItemUpdate, ListRead, ListUpdate,
)

router = APIRouter(prefix="/lists", tags=["lists"])


def _not_found(exc: Exception, detail: str) -> HTTPException:
    return HTTPException(status_code=404, detail=detail)


@router.post("", response_model=ListRead, status_code=status.HTTP_201_CREATED)
async def create_list(payload: ListCreate, user: CurrentUserDep, service: ListsServiceDep):
    return ListRead.model_validate(await service.create_list(user, payload))


@router.get("", response_model=list[ListRead])
async def list_lists(user: CurrentUserDep, service: ListsServiceDep,
                     list_status: str = Query(default="active", alias="status")):
    try:
        values = await service.list_lists(user, status=list_status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [ListRead.model_validate(value) for value in values]


@router.get("/{list_id}", response_model=ListRead)
async def get_list(list_id: uuid.UUID, user: CurrentUserDep, service: ListsServiceDep):
    try:
        return ListRead.model_validate(await service.get_list(user, list_id))
    except ListNotFoundError as exc:
        raise _not_found(exc, "List not found") from exc


@router.patch("/{list_id}", response_model=ListRead)
async def update_list(list_id: uuid.UUID, payload: ListUpdate,
                      user: CurrentUserDep, service: ListsServiceDep):
    try:
        value = await service.update_list(user, list_id, payload)
    except ListNotFoundError as exc:
        raise _not_found(exc, "List not found") from exc
    except InvalidListTransitionError as exc:
        raise HTTPException(status_code=409, detail="Invalid list transition") from exc
    return ListRead.model_validate(value)


@router.post("/{list_id}/items", response_model=ListItemRead,
             status_code=status.HTTP_201_CREATED)
async def create_item(list_id: uuid.UUID, payload: ListItemCreate,
                      user: CurrentUserDep, service: ListsServiceDep):
    try:
        item = await service.create_item(user, list_id, payload)
    except ListNotFoundError as exc:
        raise _not_found(exc, "List not found") from exc
    except InvalidListTransitionError as exc:
        raise HTTPException(status_code=409, detail="List is archived") from exc
    return ListItemRead.model_validate(item)


@router.get("/{list_id}/items", response_model=list[ListItemRead])
async def list_items(list_id: uuid.UUID, user: CurrentUserDep,
                     service: ListsServiceDep,
                     item_status: str | None = Query(default=None, alias="status")):
    try:
        items = await service.list_items(user, list_id, status=item_status)
    except ListNotFoundError as exc:
        raise _not_found(exc, "List not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [ListItemRead.model_validate(item) for item in items]


@router.get("/{list_id}/items/{item_id}", response_model=ListItemRead)
async def get_item(list_id: uuid.UUID, item_id: uuid.UUID,
                   user: CurrentUserDep, service: ListsServiceDep):
    try:
        item = await service.get_item(user, list_id, item_id)
    except (ListNotFoundError, ListItemNotFoundError) as exc:
        raise _not_found(exc, "List item not found") from exc
    return ListItemRead.model_validate(item)


@router.patch("/{list_id}/items/{item_id}", response_model=ListItemRead)
async def update_item(list_id: uuid.UUID, item_id: uuid.UUID,
                      payload: ListItemUpdate, user: CurrentUserDep,
                      service: ListsServiceDep):
    try:
        item = await service.update_item(user, list_id, item_id, payload)
    except (ListNotFoundError, ListItemNotFoundError) as exc:
        raise _not_found(exc, "List item not found") from exc
    except InvalidListTransitionError as exc:
        raise HTTPException(status_code=409, detail="List is archived") from exc
    except InvalidListItemTransitionError as exc:
        raise HTTPException(status_code=409, detail="Invalid item transition") from exc
    return ListItemRead.model_validate(item)
