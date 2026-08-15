"""HTTP routing for the Tasks capability.

Routers contain NO business logic — they delegate to the service layer and
translate domain exceptions into HTTP responses.

Routes are nested beneath the owning project. ``project_id`` arrives as a path
segment but is never trusted alone: the service verifies it belongs to
``CurrentUserDep`` before any task is read or written. Both a missing project
and a missing task yield ``404`` — never ``403`` — so neither can be probed.
There is deliberately no completion endpoint: completion is a status
transition through ``PATCH``, so there is exactly one write path.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.auth.dependencies import CurrentUserDep

from .dependencies import TasksServiceDep
from .exceptions import OwningProjectNotFoundError, TaskNotFoundError
from .schemas import TaskCreate, TaskRead, TaskUpdate

router = APIRouter(prefix="/projects/{project_id}/tasks", tags=["tasks"])


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(
    project_id: uuid.UUID,
    payload: TaskCreate,
    current_user: CurrentUserDep,
    service: TasksServiceDep,
) -> TaskRead:
    try:
        task = await service.create_task(current_user, project_id, payload)
    except OwningProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        ) from exc
    return TaskRead.model_validate(task)


@router.get("", response_model=list[TaskRead])
async def list_tasks(
    project_id: uuid.UUID,
    current_user: CurrentUserDep,
    service: TasksServiceDep,
) -> list[TaskRead]:
    try:
        tasks = await service.list_tasks(current_user, project_id)
    except OwningProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        ) from exc
    return [TaskRead.model_validate(t) for t in tasks]


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    current_user: CurrentUserDep,
    service: TasksServiceDep,
) -> TaskRead:
    try:
        task = await service.get_task(current_user, project_id, task_id)
    except OwningProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        ) from exc
    except TaskNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        ) from exc
    return TaskRead.model_validate(task)


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    payload: TaskUpdate,
    current_user: CurrentUserDep,
    service: TasksServiceDep,
) -> TaskRead:
    try:
        task = await service.update_task(
            current_user, project_id, task_id, payload
        )
    except OwningProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        ) from exc
    except TaskNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        ) from exc
    return TaskRead.model_validate(task)
