"""HTTP routing for the Identity subsystem.

Routers contain NO business logic — they delegate to the service layer
and translate domain exceptions into HTTP responses.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.transactional_email.provider import EmailDeliveryError

from .dependencies import IdentityServiceDep, RegistrationServiceDep
from .exceptions import EmailAlreadyExistsError, UserNotFoundError
from .schemas import DeviceRead, UserCreate, UserRead, UserUpdate

router = APIRouter(prefix="/identity", tags=["identity"])


@router.post(
    "/users", response_model=UserRead, status_code=status.HTTP_201_CREATED
)
async def create_user(
    payload: UserCreate, service: RegistrationServiceDep
) -> UserRead:
    try:
        user = await service.register(payload)
    except EmailAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        ) from exc
    except EmailDeliveryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "VERIFICATION_DELIVERY_FAILED",
                "message": "Account created, but verification email could not be sent. Try resending shortly.",
            },
        ) from exc
    return UserRead.model_validate(user)


@router.get("/users/{user_id}", response_model=UserRead)
async def get_user(
    user_id: uuid.UUID, service: IdentityServiceDep
) -> UserRead:
    try:
        user = await service.get_user(user_id)
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from exc
    return UserRead.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID, payload: UserUpdate, service: IdentityServiceDep
) -> UserRead:
    try:
        user = await service.update_user(user_id, payload)
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from exc
    return UserRead.model_validate(user)


@router.get("/devices/{user_id}", response_model=list[DeviceRead])
async def list_devices(
    user_id: uuid.UUID, service: IdentityServiceDep
) -> list[DeviceRead]:
    try:
        devices = await service.list_devices(user_id)
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from exc
    return [DeviceRead.model_validate(d) for d in devices]
