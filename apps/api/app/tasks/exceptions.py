"""Domain exceptions for the Tasks capability."""
from __future__ import annotations


class TasksError(Exception):
    """Base class for Tasks domain errors."""


class OwningProjectNotFoundError(TasksError):
    """Raised when the owning project is absent *or* not owned by the caller.

    Deliberately does not distinguish the two cases: a caller must not be able
    to probe for the existence of another user's projects by addressing their
    task collection. The router translates this into ``404``.
    """

    def __init__(self, project_id: str) -> None:
        super().__init__(f"Project not found: {project_id}")
        self.project_id = project_id


class TaskNotFoundError(TasksError):
    """Raised when a task is absent *or* lives under a project the caller
    does not own. Translated into ``404``, never ``403``.
    """

    def __init__(self, task_id: str) -> None:
        super().__init__(f"Task not found: {task_id}")
        self.task_id = task_id
