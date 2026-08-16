"""Activity ORM model for Rocky's Activity domain.

Activity is an append-only ledger of *what happened*. Each row records the
acting user (who), an event type (what), the entity it concerned
(entity_type + entity_id), a timestamp (when), and a small JSON payload
(enough structured metadata to reconstruct useful history).

Models hold structure only — no business logic, no ownership policy.
Ownership is direct and structural: a non-nullable FK to ``users.id``. Unlike
Task (whose ownership is transitive through a project), an Activity row *is*
the record of a user's action, so it carries ``user_id`` itself.

The reference to the concerned entity is deliberately NOT a foreign key.
Activity outlives the rows it describes: a task may be completed and later
deleted, but the history of its completion must survive. ``entity_type`` +
``entity_id`` are a soft reference, never a cascade parent.

The payload column is ``JSONB`` on Postgres and falls back to ``JSON`` on
SQLite (the test harness), so the same model is portable across both.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User

# JSONB on Postgres, plain JSON on the SQLite test harness. Mirrors the
# portable pattern already present in the repo, reproduced here so the live
# model carries no dependency on dormant modules.
JSONType = JSONB().with_variant(JSON(), "sqlite")


class Activity(Base):
    """A single recorded action taken by a user."""

    __tablename__ = "activities"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONType,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    user: Mapped["User"] = relationship(back_populates="activities")

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<Activity id={self.id!r} user_id={self.user_id!r} "
            f"event_type={self.event_type!r}>"
        )
