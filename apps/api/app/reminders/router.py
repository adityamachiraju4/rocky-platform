"""Authenticated HTTP surface for native Reminders."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.auth.dependencies import CurrentUserDep
from app.reminders.dependencies import RemindersServiceDep
from app.reminders.exceptions import (
    InvalidReminderTransitionError,
    ReminderIdempotencyConflictError,
    ReminderNotFoundError,
)
from app.reminders.schemas import ReminderCreate, ReminderRead
from app.scheduling.exceptions import ScheduledTimeInPastError

router = APIRouter(prefix="/reminders", tags=["reminders"])


@router.post("", response_model=ReminderRead, status_code=status.HTTP_201_CREATED)
async def create_reminder(
    payload: ReminderCreate,
    current_user: CurrentUserDep,
    service: RemindersServiceDep,
) -> ReminderRead:
    try:
        reminder = await service.create_reminder(current_user, payload)
    except ScheduledTimeInPastError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReminderIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail="Idempotency key conflict") from exc
    return ReminderRead.model_validate(reminder)


@router.get("", response_model=list[ReminderRead])
async def list_reminders(
    current_user: CurrentUserDep,
    service: RemindersServiceDep,
    reminder_status: str | None = Query(default=None, alias="status"),
) -> list[ReminderRead]:
    try:
        reminders = await service.list_reminders(
            current_user, status=reminder_status
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [ReminderRead.model_validate(reminder) for reminder in reminders]


@router.get("/{reminder_id}", response_model=ReminderRead)
async def get_reminder(
    reminder_id: uuid.UUID,
    current_user: CurrentUserDep,
    service: RemindersServiceDep,
) -> ReminderRead:
    try:
        reminder = await service.get_reminder(current_user, reminder_id)
    except ReminderNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Reminder not found") from exc
    return ReminderRead.model_validate(reminder)


@router.post("/{reminder_id}/complete", response_model=ReminderRead)
async def complete_reminder(
    reminder_id: uuid.UUID,
    current_user: CurrentUserDep,
    service: RemindersServiceDep,
) -> ReminderRead:
    try:
        reminder = await service.complete_reminder(current_user, reminder_id)
    except ReminderNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Reminder not found") from exc
    except InvalidReminderTransitionError as exc:
        raise HTTPException(status_code=409, detail="Invalid reminder transition") from exc
    return ReminderRead.model_validate(reminder)


@router.delete("/{reminder_id}", response_model=ReminderRead)
async def cancel_reminder(
    reminder_id: uuid.UUID,
    current_user: CurrentUserDep,
    service: RemindersServiceDep,
) -> ReminderRead:
    try:
        reminder = await service.cancel_reminder(current_user, reminder_id)
    except ReminderNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Reminder not found") from exc
    except InvalidReminderTransitionError as exc:
        raise HTTPException(status_code=409, detail="Invalid reminder transition") from exc
    return ReminderRead.model_validate(reminder)
