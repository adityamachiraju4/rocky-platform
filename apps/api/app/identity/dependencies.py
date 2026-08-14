"""Dependency-injection wiring for the Identity capability.

DI is capability-local: the Identity service is composed here, consuming
the Platform ``get_session`` provider downward. Platform never imports
this module — dependency direction is Capability -> Platform.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session

from .service import IdentityService


def get_identity_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IdentityService:
    return IdentityService(session)


IdentityServiceDep = Annotated[
    IdentityService, Depends(get_identity_service)
]

__all__ = ["get_identity_service", "IdentityServiceDep"]
