"""Business logic layer for the Tasks capability.

The service owns transactions and owns ownership policy. Every method takes
the authenticated :class:`User` and the ``project_id`` from the path, and
resolves the owning project first — a project that is absent or belongs to
someone else raises :class:`OwningProjectNotFoundError` before any task is
touched. No method accepts a caller-supplied owner identifier.

Completion semantics live here, in one place: a transition into ``complete``
stamps ``completed_at``; a transition back to ``active`` clears it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task
from app.models.user import User

from .exceptions import OwningProjectNotFoundError, TaskNotFoundError
from .repository import TaskRepository
from .schemas import TaskCreate, TaskUpdate

COMPLETE = "complete"
ACTIVE = "active"


class TasksService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tasks = TaskRepository(session)

    async def _require_owned_project(
        self, current_user: User, project_id: uuid.UUID
    ) -> None:
        project = await self._tasks.get_owned_project(
            current_user.id, project_id
        )
        if project is None:
            raise OwningProjectNotFoundError(str(project_id))

    async def create_task(
        self,
        current_user: User,
        project_id: uuid.UUID,
        data: TaskCreate,
    ) -> Task:
        await self._require_owned_project(current_user, project_id)
        task = Task(
            project_id=project_id,
            title=data.title,
            description=data.description,
            status=data.status,
            completed_at=(
                datetime.now(timezone.utc) if data.status == COMPLETE else None
            ),
        )
        await self._tasks.add(task)
        await self._session.commit()
        await self._session.refresh(task)
        return task

    async def list_tasks(
        self, current_user: User, project_id: uuid.UUID
    ) -> list[Task]:
        await self._require_owned_project(current_user, project_id)
        return await self._tasks.list_by_project(current_user.id, project_id)

    async def get_task(
        self,
        current_user: User,
        project_id: uuid.UUID,
        task_id: uuid.UUID,
    ) -> Task:
        await self._require_owned_project(current_user, project_id)
        task = await self._tasks.get_owned(
            current_user.id, project_id, task_id
        )
        if task is None:
            raise TaskNotFoundError(str(task_id))
        return task

    async def update_task(
        self,
        current_user: User,
        project_id: uuid.UUID,
        task_id: uuid.UUID,
        data: TaskUpdate,
    ) -> Task:
        task = await self.get_task(current_user, project_id, task_id)
        updates = data.model_dump(exclude_unset=True)

        new_status = updates.get("status")
        if new_status is not None and new_status != task.status:
            task.completed_at = (
                datetime.now(timezone.utc) if new_status == COMPLETE else None
            )

        for field, value in updates.items():
            setattr(task, field, value)

        await self._session.commit()
        await self._session.refresh(task)
        return task
