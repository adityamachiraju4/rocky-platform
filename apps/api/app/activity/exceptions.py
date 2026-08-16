"""Domain exceptions for the Activity capability."""
from __future__ import annotations


class ActivityError(Exception):
    """Base class for Activity domain errors."""


class ActivityNotFoundError(ActivityError):
    """Raised when an activity is absent *or* belongs to another user.

    Deliberately does not distinguish the two cases: a caller must not be able
    to probe for the existence of another user's activity by addressing it by
    id. The router translates this into ``404``, never ``403``.
    """

    def __init__(self, activity_id: str) -> None:
        super().__init__(f"Activity not found: {activity_id}")
        self.activity_id = activity_id
