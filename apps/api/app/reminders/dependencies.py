"""Dependency wiring for Reminders."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session
from app.reminders.service import RemindersService


def get_reminders_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RemindersService:
    return RemindersService(session)


RemindersServiceDep = Annotated[RemindersService, Depends(get_reminders_service)]
