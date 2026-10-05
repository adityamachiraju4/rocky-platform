"""Authenticated memory service wiring."""
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session
from app.memories.service import MemoriesService


def get_memories_service(session: Annotated[AsyncSession, Depends(get_session)]) -> MemoriesService:
    return MemoriesService(session)


MemoriesServiceDep = Annotated[MemoriesService, Depends(get_memories_service)]
