"""HTTP routing for the Authentication capability.

Routers hold NO business logic — they delegate to the lifecycle service and
translate domain exceptions into HTTP responses.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.services.auth_service import (
    InactiveUserError,
    InvalidCredentialsError,
    UnverifiedUserError,
)
from app.services.session_service import SessionError

from .dependencies import AuthServiceDep
from .exceptions import InvalidRefreshTokenError
from .schemas import LoginRequest, LogoutRequest, RefreshRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid email or password",
    headers={"WWW-Authenticate": "Bearer"},
)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, service: AuthServiceDep
) -> TokenResponse:
    try:
        return await service.login(payload)
    except InvalidCredentialsError:
        raise _INVALID_CREDENTIALS
    except InactiveUserError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        ) from exc
    except UnverifiedUserError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is not verified",
        ) from exc


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest, service: AuthServiceDep
) -> TokenResponse:
    try:
        return await service.refresh(payload.refresh_token)
    except (InvalidRefreshTokenError, SessionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutRequest, service: AuthServiceDep
) -> None:
    # Idempotent: unknown/already-revoked tokens still return 204.
    await service.logout(payload.refresh_token)
