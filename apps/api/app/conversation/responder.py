"""The Responder seam: turns a structured Outcome into Rocky's words.

This is the deliberate separation of *what happened* from *how Rocky says it*.
``ConversationService`` dispatches an action and produces an :class:`Outcome`
-- a typed record of the real result (the actual returned domain object, or
the real ledger facts). A ``Responder`` then renders that Outcome into the
reply string.

v1 ships :class:`TemplateResponder`: deterministic, grounded, no inference.
It states only what the Outcome contains. A later ``LlmResponder`` can
implement the same Protocol and phrase things naturally -- but it receives an
Outcome (facts) and returns words; it is asked "how should Rocky say this?",
never "what should Rocky do?". Execution authority stays with the resolver's
trust boundary in the service. The Responder only reads facts and speaks.

Grounded-facts rule (v1, pinned by tests): recall narrates the ledger events
it was given, and nothing more. No "you were mainly focused on X", no "you
should do Y next" -- those are reasoning/initiative behaviors reserved for the
LlmResponder. The template says what happened; it does not interpret.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

# Outcome kinds. Each maps to one rendering branch in the responder.
OutcomeKind = Literal[
    "project_list",
    "task_list",
    "task_updated",
    "activity_recall",
    "no_match",
    "target_not_found",
    "ambiguous",
]


@dataclass(frozen=True)
class RecallFact:
    """One narratable ledger event: the event type and a human label for the
    entity it concerned. Built by the dispatch from real Activity rows."""

    event_type: str
    entity_type: str
    entity_label: str | None


@dataclass(frozen=True)
class Outcome:
    """The structured result of one turn, before it becomes words.

    ``kind`` selects the rendering branch. The other fields carry the real
    facts for that branch -- only the ones relevant to ``kind`` are set. This
    is what the dispatch returns and what a Responder consumes; it never
    crosses the HTTP boundary (the service wraps the rendered reply in a
    ConversationResponse).
    """

    kind: OutcomeKind
    executed: bool
    action: str | None = None
    # project_list
    project_names: tuple[str, ...] = ()
    # task_list
    task_total: int = 0
    task_active: int = 0
    # task_updated
    task_title: str | None = None
    task_status: str | None = None
    # activity_recall
    recall_facts: tuple[RecallFact, ...] = ()
    recall_window: Literal["recent", "yesterday"] = "recent"
    # no_match / ambiguous
    candidates: tuple[str, ...] = ()
    target: str | None = None


class Responder(Protocol):
    """Renders an Outcome into a reply string. Reads facts, returns words.
    Holds no execution authority."""

    def render(self, outcome: Outcome) -> str: ...


def _quoted(value: str | None) -> str:
    return f"'{value}'" if value else ""


def _recall_phrase(fact: RecallFact) -> str:
    label = _quoted(fact.entity_label)

    if fact.event_type == "project.created":
        return f"created the {label} project" if label else "created a project"
    if fact.event_type == "project.updated":
        return f"updated the {label} project" if label else "updated a project"
    if fact.event_type == "task.created":
        return f"created the task {label}" if label else "created a task"
    if fact.event_type == "task.completed":
        return f"completed {label}" if label else "completed a task"
    if fact.event_type == "task.updated":
        return f"updated the task {label}" if label else "updated a task"

    if fact.entity_type == "project":
        return f"updated the {label} project" if label else "updated a project"
    if fact.entity_type == "task":
        return f"updated the task {label}" if label else "updated a task"
    return "recorded activity"


class TemplateResponder:
    """Deterministic, grounded renderer. Says exactly what the Outcome holds;
    interprets nothing. Throwaway once LlmResponder lands behind the Protocol.
    """

    def render(self, outcome: Outcome) -> str:
        if outcome.kind == "project_list":
            if not outcome.project_names:
                return "You have no projects yet."
            names = ", ".join(outcome.project_names)
            n = len(outcome.project_names)
            return f"You have {n} project(s): {names}."

        if outcome.kind == "task_list":
            if outcome.task_total == 0:
                return "That project has no tasks."
            return (
                f"That project has {outcome.task_total} task(s), "
                f"{outcome.task_active} active."
            )

        if outcome.kind == "task_updated":
            return (
                f"Marked '{outcome.task_title}' as {outcome.task_status}."
            )

        if outcome.kind == "activity_recall":
            if not outcome.recall_facts:
                if outcome.recall_window == "yesterday":
                    return "I don't have any activity from yesterday recorded."
                return "I don't have any recent activity recorded."
            parts = [_recall_phrase(f_) for f_ in outcome.recall_facts]
            prefix = (
                "Yesterday"
                if outcome.recall_window == "yesterday"
                else "Recently"
            )
            return prefix + ": " + ", ".join(parts) + "."

        if outcome.kind == "no_match":
            return "I'm not sure what you want me to do."

        if outcome.kind == "target_not_found":
            if outcome.target:
                return (
                    "I understood that you want to finish a task, but I "
                    f"couldn't find an active task called '{outcome.target}'."
                )
            return (
                "I understood that you want to finish a task, but I couldn't "
                "find an active task to complete."
            )

        if outcome.kind == "ambiguous":
            listed = "\n".join(f"- {c}" for c in outcome.candidates)
            return f"I found several matches:\n{listed}\nWhich one?"

        # Every OutcomeKind above is handled; reaching here is a bug.
        raise ValueError(f"unrenderable outcome kind: {outcome.kind}")
