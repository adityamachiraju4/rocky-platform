"""Device ORM model for Rocky's Identity domain.

A device belongs to exactly one user; a user may have many devices.
Models hold structure only — no business logic.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.session import Session
    from app.models.user import User


class Device(Base):
    """A device registered to a user."""

    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    device_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    device_type: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    platform: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    last_seen: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="devices")
    sessions: Mapped[list["Session"]] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<Device id={self.id!r} user_id={self.user_id!r} "
            f"name={self.device_name!r}>"
        )
