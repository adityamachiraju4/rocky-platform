"""Domain exceptions for the Projects capability."""
from __future__ import annotations


class ProjectsError(Exception):
    """Base class for Projects domain errors."""


class ProjectNotFoundError(ProjectsError):
    """Raised when a project does not exist *or* is not owned by the caller.

    Deliberately does not distinguish the two cases: a caller must not be
    able to probe for the existence of another user's projects. The router
    translates this into ``404`` for both.
    """

    def __init__(self, project_id: str) -> None:
        super().__init__(f"Project not found: {project_id}")
        self.project_id = project_id
