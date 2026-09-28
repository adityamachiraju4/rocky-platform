"""Map TypeSafe System One choices into Rocky-owned decision types."""
from __future__ import annotations

import logging
import time

from app.conversation.plans.base import ExecutablePlan
from app.intelligence.decision import (
    ConfirmationDecision,
    ConfirmationKind,
    PlanVerificationDecision,
    RouteDecision,
    RouteKind,
)
from app.intelligence.typesafe.client import TypeSafeClient, TypeSafeError, TypeSafeInvalidResponse
from app.intelligence.typesafe.models import (
    ChoiceAnswer,
    ChoiceQuestion,
    NoulAnswer,
    NoulCriteria,
    NoulQuestion,
    SystemOneRequest,
)

logger = logging.getLogger(__name__)


class TypeSafeDecisionProvider:
    def __init__(self, client: TypeSafeClient, *, model: str) -> None:
        if not model.strip():
            raise ValueError("TypeSafe model is required")
        self._client = client
        self._model = model

    async def classify_route(self, message: str) -> RouteDecision:
        answer = await self._choose(
            "route",
            state={"message": message},
            criteria={
                RouteKind.SINGLE_ACTION.value: "one supported Rocky action",
                RouteKind.MULTI_STEP_PLAN.value: "two or more ordered Rocky actions",
                RouteKind.CONVERSATION.value: "ordinary conversation or knowledge request",
                RouteKind.PERSONAL_CONTEXT.value: "needs the user's Rocky data",
                RouteKind.LIVE.value: "needs current external information",
                RouteKind.CLARIFICATION.value: "cannot be routed without clarification",
                RouteKind.UNSUPPORTED.value: "outside Rocky's supported capabilities",
            },
        )
        return RouteDecision(
            RouteKind(answer.choice), answer.confidence, answer.probabilities
        )

    async def classify_confirmation(
        self, response: str, plan_summary: str
    ) -> ConfirmationDecision:
        answer = await self._choose(
            "confirmation",
            state={"response": response, "pending_plan": plan_summary},
            criteria={
                ConfirmationKind.CONFIRM.value: "clearly approves the complete pending plan",
                ConfirmationKind.REJECT.value: "clearly rejects the complete pending plan",
                ConfirmationKind.AMEND.value: "changes, removes, or adds a plan instruction",
                ConfirmationKind.UNRELATED.value: "a separate request unrelated to the pending plan",
                ConfirmationKind.UNCERTAIN.value: "the response is not safely classifiable",
            },
        )
        return ConfirmationDecision(
            ConfirmationKind(answer.choice), answer.confidence, answer.probabilities
        )

    async def verify_plan(
        self, request: str, plan: ExecutablePlan
    ) -> PlanVerificationDecision:
        started = time.perf_counter()
        decision_type = "plan_verification"
        logger.info(
            "typesafe_decision_started",
            extra={"decision_type": decision_type, "typesafe_model": self._model},
        )
        questions = {
            "unrequested_action": NoulQuestion(
                instructions=(
                    "Does the proposed plan contain any action that was not "
                    "explicitly or reasonably requested by the user?"
                ),
                criteria=NoulCriteria(
                    true="At least one proposed action is not requested.",
                    false="Every proposed action is requested or reasonably implied.",
                ),
            ),
            "omitted_action": NoulQuestion(
                instructions=(
                    "Does the proposed plan omit any action explicitly requested "
                    "by the user?"
                ),
                criteria=NoulCriteria(
                    true="At least one explicit requested action is missing.",
                    false="All explicit requested actions are represented.",
                ),
            ),
            "excessive_mutation": NoulQuestion(
                instructions=(
                    "Does the proposed plan mutate, delete, archive, dismiss, or "
                    "otherwise change more state than the user requested?"
                ),
                criteria=NoulCriteria(
                    true="The plan changes more state than requested.",
                    false="The plan's state changes are bounded by the request.",
                ),
            ),
            "faithful": NoulQuestion(
                instructions=(
                    "Does the proposed plan faithfully represent the user's request?"
                ),
                criteria=NoulCriteria(
                    true="The complete plan faithfully represents the request.",
                    false="The plan mismatches, omits, or adds to the request.",
                ),
            ),
        }
        try:
            response = await self._client.system_one(
                SystemOneRequest(
                    state={
                        "request": request,
                        "steps": [
                            {
                                "action": step.definition.name,
                                "purpose": (
                                    step.purpose or step.definition.description
                                ),
                                "arguments": self._safe_arguments(
                                    step.arguments.model_dump(mode="json")
                                ),
                            }
                            for step in plan.steps
                        ],
                        "requires_confirmation": plan.requires_confirmation,
                    },
                    model=self._model,
                    questions=questions,
                )
            )
            probabilities: dict[str, float] = {}
            for name in questions:
                answer = response.answers.get(name)
                if not isinstance(answer, NoulAnswer):
                    raise TypeSafeInvalidResponse(
                        "TypeSafe returned an invalid plan verification answer"
                    )
                probabilities[name] = answer.noul
        except TypeSafeError as exc:
            logger.warning(
                "typesafe_decision_fallback",
                extra={
                    "decision_type": decision_type,
                    "typesafe_model": self._model,
                    "provider_error_code": exc.code,
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                },
            )
            raise
        logger.info(
            "typesafe_decision_completed",
            extra={
                "decision_type": decision_type,
                "typesafe_model": response.model,
                "probabilities": probabilities,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
            },
        )
        return PlanVerificationDecision(
            unrequested_action_probability=probabilities["unrequested_action"],
            omitted_action_probability=probabilities["omitted_action"],
            excessive_mutation_probability=probabilities["excessive_mutation"],
            faithful_probability=probabilities["faithful"],
        )

    async def _choose(
        self,
        decision_type: str,
        *,
        state: dict[str, object],
        criteria: dict[str, str],
    ) -> ChoiceAnswer:
        started = time.perf_counter()
        logger.info(
            "typesafe_decision_started",
            extra={"decision_type": decision_type, "typesafe_model": self._model},
        )
        try:
            response = await self._client.system_one(
                SystemOneRequest(
                    state=state,
                    model=self._model,
                    questions={
                        "decision": ChoiceQuestion(
                            criteria=criteria,
                            instructions=(
                                "Choose exactly one criterion. When the available state "
                                "is insufficient, choose the uncertainty or clarification criterion."
                            ),
                        )
                    },
                )
            )
            answer = response.answers.get("decision")
            if not isinstance(answer, ChoiceAnswer) or answer.choice not in criteria:
                raise TypeSafeInvalidResponse("TypeSafe returned an unknown decision")
        except TypeSafeError as exc:
            logger.warning(
                "typesafe_decision_fallback",
                extra={
                    "decision_type": decision_type,
                    "typesafe_model": self._model,
                    "provider_error_code": exc.code,
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                },
            )
            raise
        logger.info(
            "typesafe_decision_completed",
            extra={
                "decision_type": decision_type,
                "typesafe_model": response.model,
                "decision": answer.choice,
                "confidence": answer.confidence,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
            },
        )
        return answer

    @staticmethod
    def _safe_arguments(arguments: dict[str, object]) -> dict[str, object]:
        # Note bodies are unnecessary for faithfulness classification. Keep a
        # compact marker instead of exporting arbitrary private note text.
        return {
            key: ("[omitted]" if key == "content" and "title" in arguments else value)
            for key, value in arguments.items()
        }


__all__ = ["TypeSafeDecisionProvider"]
