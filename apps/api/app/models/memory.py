"""Durable explicit memories; provenance never owns their lifecycle."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User

ACTIVE = "active"
FORGOTTEN = "forgotten"
EXPLICIT_USER = "explicit_user"


class Memory(Base):
    __tablename__ = "personal_memories"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ACTIVE, server_default=ACTIVE)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default=EXPLICIT_USER, server_default=EXPLICIT_USER)
    source_thread_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    forgotten_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="memories")

    __table_args__ = (
        CheckConstraint("kind IN ('fact', 'preference')", name="ck_personal_memories_kind"),
        CheckConstraint("status IN ('active', 'forgotten')", name="ck_personal_memories_status"),
        CheckConstraint("source = 'explicit_user'", name="ck_personal_memories_source"),
        CheckConstraint("length(subject) BETWEEN 1 AND 255 AND length(trim(subject)) > 0", name="ck_personal_memories_subject"),
        CheckConstraint("length(content) BETWEEN 1 AND 2000 AND length(trim(content)) > 0", name="ck_personal_memories_content"),
        CheckConstraint("(status = 'active' AND forgotten_at IS NULL) OR (status = 'forgotten' AND forgotten_at IS NOT NULL)", name="ck_personal_memories_lifecycle"),
        Index("ix_personal_memories_user_status", "user_id", "status"),
        Index("ix_personal_memories_user_updated", "user_id", "updated_at"),
    )
