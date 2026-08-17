"""Tiny in-process conversational reference context.

This is intentionally not Memory. It stores only the last grounded task
reference per user so immediate follow-ups like "that's done too" can be
resolved safely against the current WorldView.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class GroundedTaskReference:
    title: str
    project_name: str


class ConversationContextStore:
    def __init__(self, max_users: int = 256) -> None:
        self._max_users = max_users
        self._last_task_by_user: dict[uuid.UUID, GroundedTaskReference] = {}

    def get_last_task(
        self, user_id: uuid.UUID
    ) -> GroundedTaskReference | None:
        return self._last_task_by_user.get(user_id)

    def set_last_task(
        self, user_id: uuid.UUID, ref: GroundedTaskReference
    ) -> None:
        if (
            len(self._last_task_by_user) >= self._max_users
            and user_id not in self._last_task_by_user
        ):
            oldest = next(iter(self._last_task_by_user))
            self._last_task_by_user.pop(oldest, None)
        self._last_task_by_user[user_id] = ref


conversation_context_store = ConversationContextStore()
