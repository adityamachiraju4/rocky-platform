"""Database persistence and atomic lifecycle transitions for plans."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import ConversationThread, PendingConversationPlan
from app.conversation.plans.base import ExecutablePlan, ProposedPlan

PLAN_CONFIRMATION_TTL = timedelta(minutes=15)


class PendingPlanConflictError(RuntimeError):
    """A competing lifecycle transition prevented safe plan persistence."""


class PlanLifecycleTransitionError(RuntimeError):
    """A conditional plan-state transition did not affect exactly one row."""


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class PendingPlanStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def current(
        self, user_id: uuid.UUID, thread_id: uuid.UUID, now: datetime
    ) -> PendingConversationPlan | None:
        row = await self._session.scalar(
            select(PendingConversationPlan)
            .where(
                PendingConversationPlan.user_id == user_id,
                PendingConversationPlan.thread_id == thread_id,
                PendingConversationPlan.status == "pending",
            )
            .order_by(PendingConversationPlan.created_at.desc())
            .limit(1)
        )
        if row is not None and _aware(row.expires_at) <= _aware(now):
            row.status = "expired"
            await self._session.commit()
        return row

    async def create(
        self,
        user_id: uuid.UUID,
        thread_id: uuid.UUID,
        plan: ExecutablePlan,
        now: datetime,
    ) -> PendingConversationPlan:
        # PostgreSQL serializes proposals for one owned thread. The partial
        # unique index remains the final invariant and covers other writers.
        locked_thread_id = await self._session.scalar(
            select(ConversationThread.id)
            .where(
                ConversationThread.id == thread_id,
                ConversationThread.user_id == user_id,
            )
            .with_for_update()
        )
        if locked_thread_id is None:
            await self._session.rollback()
            raise PendingPlanConflictError("conversation thread is unavailable")
        await self._session.execute(
            update(PendingConversationPlan)
            .where(
                PendingConversationPlan.user_id == user_id,
                PendingConversationPlan.thread_id == thread_id,
                PendingConversationPlan.status == "pending",
            )
            .values(status="rejected", updated_at=now)
            .execution_options(synchronize_session=False)
        )
        row = PendingConversationPlan(
            id=plan.id,
            user_id=user_id,
            thread_id=thread_id,
            status="pending",
            version=1,
            plan_payload=plan.proposed.model_dump(mode="json"),
            expires_at=now + PLAN_CONFIRMATION_TTL,
        )
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise PendingPlanConflictError(
                "a competing pending plan won creation"
            ) from exc
        await self._session.refresh(row)
        return row

    async def reject(self, row: PendingConversationPlan, now: datetime) -> bool:
        result = await self._session.execute(
            update(PendingConversationPlan)
            .where(
                PendingConversationPlan.id == row.id,
                PendingConversationPlan.user_id == row.user_id,
                PendingConversationPlan.thread_id == row.thread_id,
                PendingConversationPlan.version == row.version,
                PendingConversationPlan.status == "pending",
            )
            .values(status="rejected", updated_at=now)
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
        return result.rowcount == 1

    async def claim(self, row: PendingConversationPlan, now: datetime) -> bool:
        result = await self._session.execute(
            update(PendingConversationPlan)
            .where(
                PendingConversationPlan.id == row.id,
                PendingConversationPlan.user_id == row.user_id,
                PendingConversationPlan.thread_id == row.thread_id,
                PendingConversationPlan.version == row.version,
                PendingConversationPlan.status == "pending",
                PendingConversationPlan.expires_at > now,
            )
            .values(status="executing", updated_at=now)
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
        return result.rowcount == 1

    async def finish(
        self, row: PendingConversationPlan, *, status: str,
        result_payload: dict[str, Any], now: datetime,
    ) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("invalid terminal plan status")
        result = await self._session.execute(
            update(PendingConversationPlan)
            .where(
                PendingConversationPlan.id == row.id,
                PendingConversationPlan.user_id == row.user_id,
                PendingConversationPlan.thread_id == row.thread_id,
                PendingConversationPlan.version == row.version,
                PendingConversationPlan.status == "executing",
            )
            .values(status=status, result_payload=result_payload, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await self._session.rollback()
            raise PlanLifecycleTransitionError(
                "executing plan did not reach its terminal state"
            )
        await self._session.commit()

    @staticmethod
    def proposed(row: PendingConversationPlan) -> ProposedPlan:
        return ProposedPlan.model_validate(row.plan_payload)
