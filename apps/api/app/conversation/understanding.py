"""Typed Natural Understanding results for Rocky Conversation.

The provider may interpret language, but it returns only Rocky-owned data.
ConversationService treats these objects as untrusted proposals and still
validates registry membership, references, ownership, and state transitions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.conversation import registry
from app.conversation.resolver import WorldView


class UnderstandingProviderError(RuntimeError):
    """Raised when model-backed understanding cannot safely produce a result."""

    def __init__(self, message: str, *, code: str = "provider_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ActionProposal:
    kind: Literal["action"]
    action: str
    reference: str | None = None
    arguments: dict[str, str] | None = None
    recall_window: str | None = None


@dataclass(frozen=True)
class ConversationTurn:
    kind: Literal["conversation"]
    reply: str | None = None
    reference: str | None = None


@dataclass(frozen=True)
class PersonalContextRequest:
    """Route decision requesting a second, grounded understanding pass.

    The first understanding pass never receives Rocky's private world.  The
    provider returns this result only when answering the message requires
    personal Rocky data that cannot be represented as a direct action
    proposal.
    """

    kind: Literal["personal_context"]


@dataclass(frozen=True)
class Clarification:
    kind: Literal["clarification"]
    prompt: str | None = None
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class Unsupported:
    kind: Literal["unsupported"]
    reason: str | None = None


UnderstandingResult = (
    ActionProposal
    | ConversationTurn
    | PersonalContextRequest
    | Clarification
    | Unsupported
)


class UnderstandingProvider(Protocol):
    async def understand(
        self,
        *,
        message: str,
        world: WorldView | None,
        context: dict[str, Any] | None = None,
        include_personal_context: bool = True,
        response_language: str = "en",
    ) -> UnderstandingResult: ...


class _UnderstandingPayload(BaseModel):
    """Schema-constrained provider payload, validated again in Rocky code."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "action", "conversation", "personal_context", "clarification", "unsupported"
    ]
    action: str | None = None
    reference: str | None = None
    arguments: dict[str, str] | None = None
    recall_window: Literal["recent", "yesterday"] | None = None
    reply: str | None = Field(default=None, max_length=4000)
    prompt: str | None = Field(default=None, max_length=1000)
    candidates: list[str] | None = None
    reason: str | None = Field(default=None, max_length=1000)

    def to_result(self) -> UnderstandingResult:
        if self.kind == "action":
            if not self.action:
                raise ValueError("action result missing action")
            return ActionProposal(
                kind="action",
                action=self.action,
                reference=self.reference,
                arguments=self.arguments,
                recall_window=self.recall_window,
            )
        if self.kind == "conversation":
            return ConversationTurn(
                kind="conversation",
                reply=self.reply,
                reference=self.reference,
            )
        if self.kind == "personal_context":
            return PersonalContextRequest(kind="personal_context")
        if self.kind == "clarification":
            return Clarification(
                kind="clarification",
                prompt=self.prompt,
                candidates=tuple(self.candidates or ()),
            )
        return Unsupported(kind="unsupported", reason=self.reason)


def parse_understanding_payload(data: object) -> UnderstandingResult:
    try:
        return _UnderstandingPayload.model_validate(data).to_result()
    except (ValidationError, ValueError) as exc:
        raise UnderstandingProviderError("Malformed understanding result.") from exc


UNDERSTANDING_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kind": {
            "type": "string",
            "enum": [
                "action",
                "conversation",
                "personal_context",
                "clarification",
                "unsupported",
            ],
        },
        "action": {
            "type": ["string", "null"],
            "enum": [*registry.ACTION_NAMES, None],
        },
        "reference": {"type": ["string", "null"]},
        "arguments": {
            "type": ["object", "null"],
            "additionalProperties": {"type": "string"},
        },
        "recall_window": {
            "type": ["string", "null"],
            "enum": ["recent", "yesterday", None],
        },
        "reply": {"type": ["string", "null"], "maxLength": 4000},
        "prompt": {"type": ["string", "null"], "maxLength": 1000},
        "candidates": {
            "type": ["array", "null"],
            "items": {"type": "string"},
        },
        "reason": {"type": ["string", "null"], "maxLength": 1000},
    },
    "required": [
        "kind",
        "action",
        "reference",
        "arguments",
        "recall_window",
        "reply",
        "prompt",
        "candidates",
        "reason",
    ],
}


def safe_world_payload(world: WorldView) -> dict[str, Any]:
    """Bounded, non-secret world representation for language understanding."""

    return {
        "allowed_actions": list(registry.ACTION_NAMES),
        "action_catalog": list(registry.model_catalog()),
        "projects": [
            {"name": p.name}
            for p in world.projects[:25]
        ],
        "tasks": [
            {
                "title": t.title,
                "project": t.project_name,
                "status": t.status,
            }
            for t in world.tasks[:50]
        ],
        "reminders": [
            {
                "title": reminder.title,
                "status": reminder.status,
                "due_at": reminder.due_at.isoformat(),
                "timezone": reminder.timezone,
            }
            for reminder in world.reminders[:50]
        ],
        "notifications": [
            {"title": item.title, "body": item.body, "status": item.status}
            for item in world.notifications[:50]
        ],
        "notes": [
            {"title": note.title, "status": note.status}
            for note in world.notes[:50]
        ],
        "lists": [
            {
                "title": value.title,
                "status": value.status,
                "items": [
                    {"content": item.content, "status": item.status}
                    for item in value.items[:50]
                ],
            }
            for value in world.lists[:25]
        ],
    }
