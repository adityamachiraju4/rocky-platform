"""The closed, code-owned registry of executable actions.

This is the security boundary that makes the eventual LLM safe. A resolver
(hardcoded now, model-driven later) may only ever *propose* an action; the
name of that action must appear here or ConversationService refuses it. The
model cannot invent ``task.delete``, ``run_shell``, or ``send_email`` because
those names have no entry in this dict and therefore no executor.

v1 executable subset — three actions, enough to prove one mutation and two
reads through the orchestrator:

    project.list  — read: the user's projects
    task.list     — read: tasks in one project
    task.update   — mutate: currently only the completion transition
    activity.recall â read: recent entries from the Activity ledger

Adding an action here is a deliberate, reviewed act. The registry is the
single source of truth for what Conversation can execute.
"""
from __future__ import annotations

from typing import Final

PROJECT_LIST: Final = "project.list"
TASK_LIST: Final = "task.list"
TASK_UPDATE: Final = "task.update"
ACTIVITY_RECALL: Final = "activity.recall"

# The closed set. Membership is checked before any dispatch. Anything not in
# here is an UnknownActionError, regardless of who proposed it.
ALLOWED_ACTIONS: Final[frozenset[str]] = frozenset(
    {PROJECT_LIST, TASK_LIST, TASK_UPDATE, ACTIVITY_RECALL}
)


def is_allowed(action: str) -> bool:
    """True iff ``action`` is an executable action in the closed registry."""
    return action in ALLOWED_ACTIONS
