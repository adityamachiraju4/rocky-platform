"""native reminders domain

Revision ID: 0010_reminders_domain
Revises: 0009_scheduling_foundation
Create Date: 2026-08-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_reminders_domain"
down_revision = "0009_scheduling_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reminders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="scheduled",
            nullable=False,
        ),
        sa.Column(
            "scheduled_job_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('scheduled', 'due', 'completed', 'cancelled')",
            name="ck_reminders_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["scheduled_job_id"], ["scheduled_jobs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scheduled_job_id"),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name="uq_reminders_user_idempotency"
        ),
    )
    op.create_index(
        "ix_reminders_user_status_due",
        "reminders",
        ["user_id", "status", "due_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reminders_user_id"),
        "reminders",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_reminders_user_id"), table_name="reminders")
    op.drop_index("ix_reminders_user_status_due", table_name="reminders")
    op.drop_table("reminders")
