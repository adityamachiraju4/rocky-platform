"""HTTP routing for the Authentication capability.

Routers hold NO business logic — they delegate to the lifecycle service and
translate domain exceptions into HTTP responses.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from app.core.security import SecurityError
from app.core.settings import MissingConfigurationError
from app.services.auth_service import (
    InactiveUserError,
    InvalidCredentialsError,
    UnverifiedUserError,
)
from app.services.session_service import SessionError

from app.transactional_email.provider import EmailDeliveryError

from .action_service import InvalidAuthActionTokenError
from .dependencies import AuthActionServiceDep, AuthServiceDep
from .exceptions import InvalidRefreshTokenError
from .schemas import (
    AuthActionConfirm,
    EmailActionRequest,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    PasswordResetConfirm,
    RefreshRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

_GENERIC_EMAIL_MESSAGE = "If an eligible account exists, we've sent instructions."

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
            detail={
                "code": "EMAIL_NOT_VERIFIED",
                "message": "Verify your email to continue.",
            },
        ) from exc
    except SQLAlchemyError as exc:
        original = getattr(exc, "orig", None)
        logger.error(
            "Authentication database failure: phase=login error_type=%s "
            "database_error_code=%s",
            exc.__class__.__name__,
            getattr(original, "sqlstate", None),
            extra={
                "auth_phase": "login",
                "error_type": exc.__class__.__name__,
                "database_error_code": getattr(original, "sqlstate", None),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AUTH_SERVICE_UNAVAILABLE",
                "message": "Authentication is temporarily unavailable.",
            },
        ) from exc
    except (MissingConfigurationError, SecurityError) as exc:
        logger.error(
            "Authentication configuration failure: phase=login error_type=%s",
            exc.__class__.__name__,
            extra={
                "auth_phase": "login",
                "error_type": exc.__class__.__name__,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AUTH_SERVICE_UNAVAILABLE",
                "message": "Authentication is temporarily unavailable.",
            },
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


@router.post(
    "/email-verification/request",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_email_verification(
    payload: EmailActionRequest,
    service: AuthActionServiceDep,
) -> MessageResponse:
    try:
        await service.request_email_verification(str(payload.email))
    except EmailDeliveryError as exc:
        logger.warning("Verification email delivery failed: error_type=%s", exc.__class__.__name__)
    return MessageResponse(message=_GENERIC_EMAIL_MESSAGE)


@router.post("/email-verification/confirm", response_model=MessageResponse)
async def confirm_email_verification(
    payload: AuthActionConfirm,
    service: AuthActionServiceDep,
) -> MessageResponse:
    try:
        await service.confirm_email_verification(payload.token)
    except InvalidAuthActionTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_OR_EXPIRED_TOKEN", "message": "This verification link is invalid or expired."},
        ) from exc
    return MessageResponse(message="Email verified. You can now sign in.")


@router.post(
    "/password-reset/request",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_password_reset(
    payload: EmailActionRequest,
    service: AuthActionServiceDep,
) -> MessageResponse:
    try:
        await service.request_password_reset(str(payload.email))
    except EmailDeliveryError as exc:
        logger.warning("Password reset email delivery failed: error_type=%s", exc.__class__.__name__)
    return MessageResponse(message=_GENERIC_EMAIL_MESSAGE)


@router.post("/password-reset/confirm", response_model=MessageResponse)
async def confirm_password_reset(
    payload: PasswordResetConfirm,
    service: AuthActionServiceDep,
) -> MessageResponse:
    try:
        await service.confirm_password_reset(payload.token, payload.new_password)
    except InvalidAuthActionTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_OR_EXPIRED_TOKEN", "message": "This password reset link is invalid or expired."},
        ) from exc
    return MessageResponse(message="Password updated. Sign in with your new password.")
