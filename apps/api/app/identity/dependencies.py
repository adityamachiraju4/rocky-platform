"""Dependency-injection wiring for the Identity subsystem.

Provides ``IdentityServiceDep`` for routers to depend on, constructing an
:class:`IdentityService` from a per-request async database session.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session

from .service import IdentityService


def get_identity_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IdentityService:
    """Construct an :class:`IdentityService` bound to the request session."""
    return IdentityService(session)


IdentityServiceDep = Annotated[
    IdentityService, Depends(get_identity_service)
]
