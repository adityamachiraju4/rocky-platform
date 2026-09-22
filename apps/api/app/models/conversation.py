"""Durable, bounded, user-owned conversation state."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User

JSONType = JSONB().with_variant(JSON(), "sqlite")


class ConversationThread(Base):
    __tablename__ = "conversation_threads"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # NULL permits any number of explicit threads. The literal "default" is
    # unique per user and is used by clients that omit thread_id.
    default_key: Mapped[str | None] = mapped_column(String(16), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="conversation_threads")
    turns: Mapped[list["ConversationTurnRecord"]] = relationship(
        back_populates="thread", cascade="all, delete-orphan", passive_deletes=True
    )
    grounded_references: Mapped[list["GroundedReference"]] = relationship(
        back_populates="thread", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint(
            "default_key IS NULL OR default_key = 'default'",
            name="ck_conversation_threads_default_key",
        ),
        UniqueConstraint(
            "user_id", "default_key", name="uq_conversation_threads_user_default"
        ),
        Index("ix_conversation_threads_user_updated", "user_id", "updated_at"),
    )


class ConversationTurnRecord(Base):
    __tablename__ = "conversation_turns"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(35), nullable=True)
    turn_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    thread: Mapped[ConversationThread] = relationship(back_populates="turns")

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')", name="ck_conversation_turns_role"
        ),
        Index("ix_conversation_turns_thread_created", "thread_id", "created_at"),
    )


class GroundedReference(Base):
    __tablename__ = "conversation_grounded_references"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    display_text: Mapped[str] = mapped_column(String(500), nullable=False)
    reference_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now()
    )

    thread: Mapped[ConversationThread] = relationship(
        back_populates="grounded_references"
    )

    __table_args__ = (
        UniqueConstraint(
            "thread_id", "kind", name="uq_conversation_grounded_thread_kind"
        ),
        Index(
            "ix_conversation_grounded_thread_updated", "thread_id", "updated_at"
        ),
    )
