"""Dependency wiring for Lists."""
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session
from app.lists.service import ListsService


def get_lists_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ListsService:
    return ListsService(session)


ListsServiceDep = Annotated[ListsService, Depends(get_lists_service)]
