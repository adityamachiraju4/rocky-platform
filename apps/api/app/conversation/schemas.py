"""Pydantic v2 schemas for the Conversation orchestration layer.

``ResolvedAction`` is the sole output of a resolver and the sole input to the
dispatch step: a validated action name plus its arguments. Keeping it a typed
object (not a free dict) means the trust boundary in ConversationService has
something concrete to validate.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class ResolvedAction(BaseModel):
    """A single action a resolver proposes for execution.

    ``action`` must match a name in the closed registry; ConversationService
    verifies this before dispatch. The id fields are optional because reads
    like ``project.list`` need none, while ``task.update`` needs both.
    """

    model_config = ConfigDict(frozen=True)

    action: str
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    status: str | None = None
    recall_window: str | None = None


class ConversationRequest(BaseModel):
    """One turn of input: a natural-language message. Nothing else — no ids,
    no owner, no session. Ownership derives from the authenticated user."""

    message: str = Field(min_length=1, max_length=2000)
    timezone: str | None = Field(default=None, max_length=128)


class ConversationResponse(BaseModel):
    """The truthful result of one turn.

    ``executed`` is False for honest non-executions (no match, ambiguity):
    the reply explains why and nothing was mutated. When True, ``action``
    names what ran and ``reply`` describes what actually happened, built from
    the service's real returned object — never from the resolver's claim.
    """

    executed: bool
    action: str | None = None
    reply: str
