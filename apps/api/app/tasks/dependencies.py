"""Dependency-injection wiring for the Tasks capability.

Capability-local DI: the service is composed here from the Platform
``get_session`` provider. Platform never imports this module — dependency
direction is Capability -> Platform.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_session

from .service import TasksService


def get_tasks_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TasksService:
    return TasksService(session)


TasksServiceDep = Annotated[TasksService, Depends(get_tasks_service)]

__all__ = ["get_tasks_service", "TasksServiceDep"]
