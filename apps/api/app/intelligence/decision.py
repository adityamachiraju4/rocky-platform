"""Rocky-owned contracts for optional structured decision providers.

Decisions are advisory inputs. Authorization, grounding, validation,
confirmation policy, and execution remain owned by Rocky code.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.conversation.plans.base import ExecutablePlan


class DecisionProviderError(RuntimeError):
    """A structured decision provider could not return a trusted decision."""


class RouteKind(StrEnum):
    SINGLE_ACTION = "single_action"
    MULTI_STEP_PLAN = "multi_step_plan"
    CONVERSATION = "conversation"
    PERSONAL_CONTEXT = "personal_context"
    LIVE = "live"
    CLARIFICATION = "clarification"
    UNSUPPORTED = "unsupported"


class ConfirmationKind(StrEnum):
    CONFIRM = "confirm"
    REJECT = "reject"
    AMEND = "amend"
    UNRELATED = "unrelated"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class RouteDecision:
    kind: RouteKind
    confidence: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class ConfirmationDecision:
    kind: ConfirmationKind
    confidence: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class PlanVerificationDecision:
    """Independent yes-probabilities returned by focused verification checks."""

    unrequested_action_probability: float
    omitted_action_probability: float
    excessive_mutation_probability: float
    faithful_probability: float

    @property
    def concern_probabilities(self) -> dict[str, float]:
        return {
            "unrequested_action": self.unrequested_action_probability,
            "omitted_action": self.omitted_action_probability,
            "excessive_mutation": self.excessive_mutation_probability,
        }


@dataclass(frozen=True)
class DecisionPolicy:
    """Conservative, code-owned thresholds that are easy to tune together.

    Confirmation uses the strictest threshold because it can unlock mutation.
    Noul values are probabilities of "yes", not answer confidence. A concern
    blocks once it crosses its check-specific threshold. A plan passes cleanly
    only when faithfulness is strong and every concern is clearly low; the
    middle band remains ambiguous and follows optional/required policy.
    """

    route_enabled: bool = True
    confirmation_enabled: bool = True
    plan_verification_enabled: bool = True
    plan_verification_required: bool = False
    route_min_confidence: float = 0.85
    confirmation_min_confidence: float = 0.90
    verification_unrequested_action_probability: float = 0.65
    verification_omitted_action_probability: float = 0.70
    verification_excessive_mutation_probability: float = 0.55
    verification_faithful_probability: float = 0.65
    verification_clear_concern_max_probability: float = 0.35


class DecisionProvider(Protocol):
    async def classify_route(self, message: str) -> RouteDecision: ...

    async def classify_confirmation(
        self, response: str, plan_summary: str
    ) -> ConfirmationDecision: ...

    async def verify_plan(
        self, request: str, plan: ExecutablePlan
    ) -> PlanVerificationDecision: ...


__all__ = [
    "ConfirmationDecision",
    "ConfirmationKind",
    "DecisionPolicy",
    "DecisionProvider",
    "DecisionProviderError",
    "PlanVerificationDecision",
    "RouteDecision",
    "RouteKind",
]
