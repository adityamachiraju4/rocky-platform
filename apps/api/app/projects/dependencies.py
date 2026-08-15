"""Dependency-injection wiring for the Projects capability.

Capability-local DI: the service is composed here from the Platform
``get_session`` provider. Platform never imports this module — dependency
direction is Capability -> Platform.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session

from .service import ProjectsService


def get_projects_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectsService:
    return ProjectsService(session)


ProjectsServiceDep = Annotated[
    ProjectsService, Depends(get_projects_service)
]

__all__ = ["get_projects_service", "ProjectsServiceDep"]
