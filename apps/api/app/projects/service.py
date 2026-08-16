"""Business logic layer for the Projects capability.

The service owns transactions and owns ownership policy: every method takes
the authenticated :class:`User` and scopes all persistence by that user's id.
No method accepts a caller-supplied owner identifier.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.user import User

from app.activity.recorder import ActivityRecorder

from .exceptions import ProjectNotFoundError
from .repository import ProjectRepository
from .schemas import ProjectCreate, ProjectUpdate


class ProjectsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._projects = ProjectRepository(session)
        self._activity = ActivityRecorder(session)

    async def create_project(
        self, current_user: User, data: ProjectCreate
    ) -> Project:
        project = Project(
            user_id=current_user.id,
            name=data.name,
            description=data.description,
            status=data.status,
        )
        await self._projects.add(project)
        await self._activity.record(
            user_id=current_user.id,
            event_type="project.created",
            entity_type="project",
            entity_id=project.id,
            payload={"name": project.name, "status": project.status},
        )
        await self._session.commit()
        await self._session.refresh(project)
        return project

    async def list_projects(self, current_user: User) -> list[Project]:
        return await self._projects.list_by_user_id(current_user.id)

    async def get_project(
        self, current_user: User, project_id: uuid.UUID
    ) -> Project:
        project = await self._projects.get_owned(current_user.id, project_id)
        if project is None:
            raise ProjectNotFoundError(str(project_id))
        return project

    async def update_project(
        self,
        current_user: User,
        project_id: uuid.UUID,
        data: ProjectUpdate,
    ) -> Project:
        project = await self.get_project(current_user, project_id)
        updates = data.model_dump(exclude_unset=True)
        changed_fields = [
            field
            for field, value in updates.items()
            if getattr(project, field) != value
        ]
        for field in changed_fields:
            setattr(project, field, updates[field])
        if changed_fields:
            await self._activity.record(
                user_id=current_user.id,
                event_type="project.updated",
                entity_type="project",
                entity_id=project.id,
                payload={"changed_fields": changed_fields},
            )
        await self._session.commit()
        await self._session.refresh(project)
        return project
