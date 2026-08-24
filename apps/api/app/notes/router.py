"""Authenticated HTTP surface for native Notes."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.auth.dependencies import CurrentUserDep
from app.notes.dependencies import NotesServiceDep
from app.notes.exceptions import InvalidNoteTransitionError, NoteNotFoundError
from app.notes.schemas import NoteCreate, NoteRead, NoteUpdate

router = APIRouter(prefix="/notes", tags=["notes"])


@router.post("", response_model=NoteRead, status_code=status.HTTP_201_CREATED)
async def create_note(
    payload: NoteCreate,
    current_user: CurrentUserDep,
    service: NotesServiceDep,
) -> NoteRead:
    return NoteRead.model_validate(await service.create_note(current_user, payload))


@router.get("", response_model=list[NoteRead])
async def list_notes(
    current_user: CurrentUserDep,
    service: NotesServiceDep,
    note_status: str = Query(default="active", alias="status"),
) -> list[NoteRead]:
    try:
        notes = await service.list_notes(current_user, status=note_status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [NoteRead.model_validate(note) for note in notes]


@router.get("/{note_id}", response_model=NoteRead)
async def get_note(
    note_id: uuid.UUID,
    current_user: CurrentUserDep,
    service: NotesServiceDep,
) -> NoteRead:
    try:
        note = await service.get_note(current_user, note_id)
    except NoteNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Note not found") from exc
    return NoteRead.model_validate(note)


@router.patch("/{note_id}", response_model=NoteRead)
async def update_note(
    note_id: uuid.UUID,
    payload: NoteUpdate,
    current_user: CurrentUserDep,
    service: NotesServiceDep,
) -> NoteRead:
    try:
        note = await service.update_note(current_user, note_id, payload)
    except NoteNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Note not found") from exc
    except InvalidNoteTransitionError as exc:
        raise HTTPException(status_code=409, detail="Invalid note transition") from exc
    return NoteRead.model_validate(note)
