"""Tiny in-process conversational reference context.

This is intentionally not Memory. It stores only the last grounded references
and one bounded general-assistant exchange per user so immediate follow-ups can
be resolved without sending an unbounded chat history.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class GroundedProjectReference:
    name: str


@dataclass(frozen=True)
class GroundedTaskReference:
    title: str
    project_name: str


@dataclass(frozen=True)
class GeneralConversationTurn:
    message: str
    reply: str
    language: str


class ConversationContextStore:
    def __init__(self, max_users: int = 256) -> None:
        self._max_users = max_users
        self._last_project_by_user: dict[
            uuid.UUID, GroundedProjectReference
        ] = {}
        self._last_task_by_user: dict[uuid.UUID, GroundedTaskReference] = {}
        self._last_general_turn_by_user: dict[
            uuid.UUID, GeneralConversationTurn
        ] = {}

    def get_last_project(
        self, user_id: uuid.UUID
    ) -> GroundedProjectReference | None:
        return self._last_project_by_user.get(user_id)

    def set_last_project(
        self, user_id: uuid.UUID, ref: GroundedProjectReference
    ) -> None:
        if (
            len(self._last_project_by_user) >= self._max_users
            and user_id not in self._last_project_by_user
        ):
            oldest = next(iter(self._last_project_by_user))
            self._last_project_by_user.pop(oldest, None)
        self._last_project_by_user[user_id] = ref

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

    def get_last_general_turn(
        self, user_id: uuid.UUID
    ) -> GeneralConversationTurn | None:
        return self._last_general_turn_by_user.get(user_id)

    def set_last_general_turn(
        self, user_id: uuid.UUID, turn: GeneralConversationTurn
    ) -> None:
        if (
            len(self._last_general_turn_by_user) >= self._max_users
            and user_id not in self._last_general_turn_by_user
        ):
            oldest = next(iter(self._last_general_turn_by_user))
            self._last_general_turn_by_user.pop(oldest, None)
        self._last_general_turn_by_user[user_id] = turn


conversation_context_store = ConversationContextStore()
