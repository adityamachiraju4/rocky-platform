"""Explicit authenticated memory operations; no physical-delete endpoint."""
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.auth.dependencies import CurrentUserDep
from app.memories.dependencies import MemoriesServiceDep
from app.memories.exceptions import InvalidMemoryTransitionError, MemoryNotFoundError
from app.memories.schemas import MemoryCreate, MemoryRead, MemoryUpdate

router = APIRouter(prefix="/memories", tags=["memories"])


@router.post("", response_model=MemoryRead, status_code=201)
async def remember(payload: MemoryCreate, user: CurrentUserDep, service: MemoriesServiceDep) -> MemoryRead:
    return MemoryRead.model_validate(await service.remember(user, payload))


@router.get("", response_model=list[MemoryRead])
async def list_memories(user: CurrentUserDep, service: MemoriesServiceDep,
                        memory_status: Literal["active", "forgotten"] = Query(default="active", alias="status")) -> list[MemoryRead]:
    return [MemoryRead.model_validate(m) for m in await service.list_memories(user, status=memory_status)]


@router.get("/{memory_id}", response_model=MemoryRead)
async def get_memory(memory_id: uuid.UUID, user: CurrentUserDep, service: MemoriesServiceDep) -> MemoryRead:
    try:
        return MemoryRead.model_validate(await service.get_memory(user, memory_id))
    except MemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc


@router.patch("/{memory_id}", response_model=MemoryRead)
async def update_memory(memory_id: uuid.UUID, payload: MemoryUpdate,
                        user: CurrentUserDep, service: MemoriesServiceDep) -> MemoryRead:
    try:
        return MemoryRead.model_validate(await service.update_memory(user, memory_id, payload))
    except MemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc
    except InvalidMemoryTransitionError as exc:
        raise HTTPException(status_code=409, detail="Invalid memory transition") from exc
