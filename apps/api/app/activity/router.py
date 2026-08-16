"""HTTP routing for the Activity capability — READ-ONLY.

Routers contain NO business logic — they delegate to the service layer and
translate domain exceptions into HTTP responses.

There is deliberately no POST/PATCH/DELETE: activity is emitted by the domain
services, never created through the public API. Reads are scoped to
``CurrentUserDep``; an activity that is absent or belongs to another user
yields ``404`` — never ``403`` — so it cannot be probed.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.auth.dependencies import CurrentUserDep

from .dependencies import ActivityServiceDep
from .exceptions import ActivityNotFoundError
from .schemas import ActivityRead

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("", response_model=list[ActivityRead])
async def list_activities(
    current_user: CurrentUserDep,
    service: ActivityServiceDep,
) -> list[ActivityRead]:
    activities = await service.list_activities(current_user)
    return [ActivityRead.model_validate(a) for a in activities]


@router.get("/{activity_id}", response_model=ActivityRead)
async def get_activity(
    activity_id: uuid.UUID,
    current_user: CurrentUserDep,
    service: ActivityServiceDep,
) -> ActivityRead:
    try:
        activity = await service.get_activity(current_user, activity_id)
    except ActivityNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found"
        ) from exc
    return ActivityRead.model_validate(activity)
