"""Dependency wiring for Notes."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session
from app.notes.service import NotesService


def get_notes_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NotesService:
    return NotesService(session)


NotesServiceDep = Annotated[NotesService, Depends(get_notes_service)]
