"""Database access layer for the Projects capability.

Repositories perform persistence operations only — no business logic,
no transaction management. Committing is the service's responsibility.

Every read is scoped by ``user_id``. A project belonging to another user is
never loaded into memory, so ownership cannot be leaked by a later mistake.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, project: Project) -> Project:
        self._session.add(project)
        await self._session.flush()
        return project

    async def get_owned(
        self, user_id: uuid.UUID, project_id: uuid.UUID
    ) -> Project | None:
        result = await self._session.execute(
            select(Project).where(
                Project.id == project_id,
                Project.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_user_id(self, user_id: uuid.UUID) -> list[Project]:
        result = await self._session.execute(
            select(Project)
            .where(Project.user_id == user_id)
            .order_by(Project.created_at.desc())
        )
        return list(result.scalars().all())
