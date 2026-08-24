"""Authenticated HTTP surface for native Notifications."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query

from app.auth.dependencies import CurrentUserDep
from app.notifications.dependencies import NotificationsServiceDep
from app.notifications.exceptions import (
    InvalidNotificationTransitionError,
    NotificationNotFoundError,
)
from app.notifications.schemas import NotificationRead, NotificationUpdate

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationRead])
async def list_notifications(
    current_user: CurrentUserDep,
    service: NotificationsServiceDep,
    notification_status: str | None = Query(default=None, alias="status"),
) -> list[NotificationRead]:
    try:
        notifications = await service.list_notifications(
            current_user, status=notification_status
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [NotificationRead.model_validate(item) for item in notifications]


@router.get("/{notification_id}", response_model=NotificationRead)
async def get_notification(
    notification_id: uuid.UUID,
    current_user: CurrentUserDep,
    service: NotificationsServiceDep,
) -> NotificationRead:
    try:
        notification = await service.get_notification(current_user, notification_id)
    except NotificationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Notification not found") from exc
    return NotificationRead.model_validate(notification)


@router.patch("/{notification_id}", response_model=NotificationRead)
async def update_notification(
    notification_id: uuid.UUID,
    payload: NotificationUpdate,
    current_user: CurrentUserDep,
    service: NotificationsServiceDep,
) -> NotificationRead:
    try:
        if payload.status == "read":
            notification = await service.mark_read(current_user, notification_id)
        else:
            notification = await service.dismiss(current_user, notification_id)
    except NotificationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Notification not found") from exc
    except InvalidNotificationTransitionError as exc:
        raise HTTPException(status_code=409, detail="Invalid notification transition") from exc
    return NotificationRead.model_validate(notification)
