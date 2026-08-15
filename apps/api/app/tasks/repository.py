"""Database access layer for the Tasks capability.

Repositories perform persistence operations only — no business logic, no
transaction management. Committing is the service's responsibility.

Every read joins ``projects`` and filters on ``Project.user_id``. A task under
another user's project is never loaded into memory, so ownership cannot be
leaked by a later mistake. ``Project`` is imported from the Foundation model
package, not from the Projects capability — capabilities never import one
another.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.task import Task


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, task: Task) -> Task:
        self._session.add(task)
        await self._session.flush()
        return task

    async def get_owned_project(
        self, user_id: uuid.UUID, project_id: uuid.UUID
    ) -> Project | None:
        """Resolve the owning project, scoped to the caller."""
        result = await self._session.execute(
            select(Project).where(
                Project.id == project_id,
                Project.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_owned(
        self,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        task_id: uuid.UUID,
    ) -> Task | None:
        """Load a task only if it sits under a project the caller owns."""
        result = await self._session.execute(
            select(Task)
            .join(Project, Task.project_id == Project.id)
            .where(
                Task.id == task_id,
                Task.project_id == project_id,
                Project.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_project(
        self, user_id: uuid.UUID, project_id: uuid.UUID
    ) -> list[Task]:
        result = await self._session.execute(
            select(Task)
            .join(Project, Task.project_id == Project.id)
            .where(
                Task.project_id == project_id,
                Project.user_id == user_id,
            )
            .order_by(Task.created_at.desc())
        )
        return list(result.scalars().all())
