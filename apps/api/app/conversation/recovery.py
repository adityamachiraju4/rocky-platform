"""Bounded understanding pass state; recovery has no retrieval/execution authority."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.conversation.context import RECENT_CHARACTER_BUDGET, RECENT_TURN_LIMIT
from app.conversation.understanding import Clarification

# One normal pass, one CI-6 retrieved pass, and at most one CI-7 recovery.
MAX_UNDERSTANDING_PASSES = 3
# Prior-result storage already caps the user message and reply at 2,000 each.
RECOVERY_PRIOR_TEXT_CHARS = 2000


class ContextSource(StrEnum):
    NONE = "none"
    TRUSTED = "trusted"
    RETRIEVED = "retrieved"


class PassKind(StrEnum):
    NORMAL = "normal"
    RETRIEVED = "retrieved"
    RECOVERY = "recovery"


@dataclass(frozen=True)
class UnderstandingPassState:
    context_source: ContextSource
    kind: PassKind = PassKind.NORMAL

    @property
    def can_retrieve(self) -> bool:
        return self.context_source is ContextSource.NONE and self.kind is PassKind.NORMAL

    @property
    def can_recover(self) -> bool:
        return self.context_source is not ContextSource.NONE and self.kind is not PassKind.RECOVERY

    def after_retrieval(self) -> UnderstandingPassState:
        if not self.can_retrieve:
            raise ValueError("personal retrieval is unavailable for this pass")
        return UnderstandingPassState(ContextSource.RETRIEVED, PassKind.RETRIEVED)

    def after_recovery(self) -> UnderstandingPassState:
        if not self.can_recover:
            raise ValueError("understanding recovery is unavailable for this pass")
        return UnderstandingPassState(self.context_source, PassKind.RECOVERY)


def recovery_context(
    original: dict[str, Any] | None,
    continuity: dict[str, Any] | None,
    clarification: Clarification,
) -> dict[str, Any] | None:
    """Add withheld, already-owned thread continuity only if it adds facts.

    The service gates this to grounded passes. No references, records, or
    context from another thread can be fetched by this function.
    """
    original = original or {}
    additional = {
        key: value for key, value in (continuity or {}).items()
        if key in {"recent_turns", "previous_general_turn"}
        and value and value != original.get(key)
    }
    if not additional:
        return None
    # Enforce the existing conversation budget again at this provider seam.
    # In particular, a single long stored user turn must not bypass it.
    if "recent_turns" in additional:
        remaining = RECENT_CHARACTER_BUDGET
        turns = []
        for turn in additional["recent_turns"][-RECENT_TURN_LIMIT:]:
            if remaining <= 0:
                break
            content = turn["content"][:remaining]
            remaining -= len(content)
            turns.append({**turn, "content": content})
        additional["recent_turns"] = turns
    if "previous_general_turn" in additional:
        prior = additional["previous_general_turn"]
        additional["previous_general_turn"] = {
            "message": prior.get("message", "")[:RECOVERY_PRIOR_TEXT_CHARS],
            "reply": prior.get("reply", "")[:RECOVERY_PRIOR_TEXT_CHARS],
            "language": prior.get("language", "en"),
        }
    return {
        **original,
        **additional,
        "understanding_recovery": {
            "prompt": clarification.prompt,
            "candidates": list(clarification.candidates),
        },
    }
