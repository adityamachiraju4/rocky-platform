"""Typed Natural Understanding results for Rocky Conversation.

The provider may interpret language, but it returns only Rocky-owned data.
ConversationService treats these objects as untrusted proposals and still
validates registry membership, references, ownership, and state transitions.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.conversation import registry
from app.conversation.plans.base import ProposedPlan, ProposedPlanStep
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
class PlanProposal:
    kind: Literal["plan"]
    plan: ProposedPlan


@dataclass(frozen=True)
class ConversationTurn:
    kind: Literal["conversation"]
    reply: str | None = None
    reference: str | None = None


PERSONAL_QUERY_MAX = 200
PersonalScope = Literal["projects", "tasks", "reminders", "notifications", "notes", "lists"]
PERSONAL_SCOPES = ("projects", "tasks", "reminders", "notifications", "notes", "lists")


class PersonalRetrievalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1, max_length=PERSONAL_QUERY_MAX)
    scopes: list[PersonalScope] = Field(min_length=1, max_length=6)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()

    @field_validator("scopes")
    @classmethod
    def unique_scopes(cls, value: list[PersonalScope]) -> list[PersonalScope]:
        if len(set(value)) != len(value):
            raise ValueError("scopes must be unique")
        return value


@dataclass(frozen=True)
class PersonalContextRequest:
    """Route decision requesting a second, grounded understanding pass.

    The first understanding pass never receives Rocky's private world.  The
    provider returns this result only when answering the message requires
    personal Rocky data that cannot be represented as a direct action
    proposal.
    """

    kind: Literal["personal_context"]
    query: str
    scopes: tuple[PersonalScope, ...]

    def validated(self) -> PersonalRetrievalPayload:
        if self.kind != "personal_context":
            raise ValueError("invalid retrieval kind")
        return PersonalRetrievalPayload.model_validate(
            {"query": self.query, "scopes": list(self.scopes)}
        )


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
    | PlanProposal
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
        "action", "plan", "conversation", "personal_context", "clarification", "unsupported"
    ]
    action: str | None = None
    reference: str | None = None
    arguments: dict[str, str] | None = None
    recall_window: Literal["recent", "yesterday"] | None = None
    reply: str | None = Field(default=None, max_length=4000)
    prompt: str | None = Field(default=None, max_length=1000)
    candidates: list[str] | None = None
    reason: str | None = Field(default=None, max_length=1000)
    steps: list[ProposedPlanStep] | None = None
    summary: str | None = Field(default=None, max_length=1000)
    retrieval: PersonalRetrievalPayload | None = None

    def to_result(self) -> UnderstandingResult:
        if self.kind != "personal_context" and self.retrieval is not None:
            raise ValueError("retrieval belongs only to personal_context")
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
        if self.kind == "plan":
            return PlanProposal(
                kind="plan",
                plan=ProposedPlan(steps=self.steps or [], summary=self.summary),
            )
        if self.kind == "conversation":
            return ConversationTurn(
                kind="conversation",
                reply=self.reply,
                reference=self.reference,
            )
        if self.kind == "personal_context":
            if any(getattr(self, field) is not None for field in (
                "action", "reference", "arguments", "recall_window", "reply",
                "prompt", "candidates", "reason", "steps", "summary",
            )):
                raise ValueError("personal_context cannot carry other proposals")
            if self.retrieval is None:
                raise ValueError("personal_context requires retrieval")
            return PersonalContextRequest(
                kind="personal_context", query=self.retrieval.query,
                scopes=tuple(self.retrieval.scopes),
            )
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


def _model_plan_step_schema() -> dict[str, Any]:
    """Build the discriminated step contract from the CI-3 registry."""

    def strict_arguments_schema() -> dict[str, Any]:
        schema = deepcopy(definition.argument_schema())

        def clean(value: object) -> None:
            if isinstance(value, dict):
                value.pop("default", None)
                value.pop("title", None)
                for key, nested in value.items():
                    if key == "properties" and isinstance(nested, dict):
                        for property_schema in nested.values():
                            clean(property_schema)
                    else:
                        clean(nested)
            elif isinstance(value, list):
                for nested in value:
                    clean(nested)

        clean(schema)
        properties = schema.get("properties", {})
        schema["required"] = list(properties)
        return schema

    variants: list[dict[str, Any]] = []
    for definition in registry.ACTION_REGISTRY.definitions:
        variants.append({
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "pattern": "^step_[1-8]$"},
                "action": {"type": "string", "const": definition.name},
                "arguments": strict_arguments_schema(),
                "reference": {"type": ["string", "null"], "maxLength": 500},
                "result_of": {"type": ["string", "null"], "pattern": "^step_[1-8]$"},
                "depends_on": {
                    "type": "array", "maxItems": 7,
                    "items": {"type": "string", "pattern": "^step_[1-8]$"},
                },
                "purpose": {"type": ["string", "null"], "maxLength": 500},
            },
            "required": [
                "id", "action", "arguments", "reference", "result_of",
                "depends_on", "purpose",
            ],
        })
    return {"anyOf": variants}


UNDERSTANDING_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kind": {
            "type": "string",
            "enum": [
                "action",
                "plan",
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
        "steps": {
            "type": ["array", "null"],
            "minItems": 2,
            "maxItems": 8,
            "items": _model_plan_step_schema(),
        },
        "summary": {"type": ["string", "null"], "maxLength": 1000},
        "retrieval": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": PERSONAL_QUERY_MAX},
                        "scopes": {"type": "array", "minItems": 1, "maxItems": 6,
                                   "items": {"type": "string", "enum": list(PERSONAL_SCOPES)}},
                    },
                    "required": ["query", "scopes"],
                },
            ],
        },
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
        "steps",
        "summary",
        "retrieval",
    ],
}


def safe_world_payload(world: WorldView) -> dict[str, Any]:
    """Bounded, non-secret world representation for language understanding."""

    return {
        "allowed_actions": list(registry.ACTION_NAMES),
        "action_catalog": list(registry.model_catalog()),
        "projects": [
            {"name": p.name[:200]}
            for p in world.projects[:25]
        ],
        "tasks": [
            {
                "title": t.title[:200],
                "project": t.project_name[:200],
                "status": t.status,
            }
            for t in world.tasks[:50]
        ],
        "reminders": [
            {
                "title": reminder.title[:200],
                "status": reminder.status,
                "due_at": reminder.due_at.isoformat(),
                "timezone": reminder.timezone[:100],
            }
            for reminder in world.reminders[:50]
        ],
        "notifications": [
            {"title": item.title[:200], "body": item.body[:1000], "status": item.status}
            for item in world.notifications[:50]
        ],
        "notes": [
            {"title": note.title[:200], "content": note.content[:1000], "status": note.status}
            for note in world.notes[:50]
        ],
        "lists": [
            {
                "title": value.title[:200],
                "status": value.status,
                "items": [
                    {"content": item.content[:1000], "status": item.status}
                    for item in value.items[:8]
                ],
            }
            for value in world.lists[:25]
        ],
    }
