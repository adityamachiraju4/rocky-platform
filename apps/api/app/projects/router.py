"""HTTP routing for the Projects capability.

Routers contain NO business logic — they delegate to the service layer and
translate domain exceptions into HTTP responses.

Every route is authenticated and ownership-scoped: the owner comes from
``CurrentUserDep``, so no path or body ever carries a ``user_id``. A project
belonging to another user is indistinguishable from one that does not exist.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.auth.dependencies import CurrentUserDep

from .dependencies import ProjectsServiceDep
from .exceptions import ProjectNotFoundError
from .schemas import ProjectCreate, ProjectRead, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post(
    "", response_model=ProjectRead, status_code=status.HTTP_201_CREATED
)
async def create_project(
    payload: ProjectCreate,
    current_user: CurrentUserDep,
    service: ProjectsServiceDep,
) -> ProjectRead:
    project = await service.create_project(current_user, payload)
    return ProjectRead.model_validate(project)


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    current_user: CurrentUserDep,
    service: ProjectsServiceDep,
) -> list[ProjectRead]:
    projects = await service.list_projects(current_user)
    return [ProjectRead.model_validate(p) for p in projects]


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: uuid.UUID,
    current_user: CurrentUserDep,
    service: ProjectsServiceDep,
) -> ProjectRead:
    try:
        project = await service.get_project(current_user, project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        ) from exc
    return ProjectRead.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    current_user: CurrentUserDep,
    service: ProjectsServiceDep,
) -> ProjectRead:
    try:
        project = await service.update_project(
            current_user, project_id, payload
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        ) from exc
    return ProjectRead.model_validate(project)
