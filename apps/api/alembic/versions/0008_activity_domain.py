"""activity domain: activities

Creates the Activity capability table — an append-only ledger of user actions.
Ownership is direct and structural: a non-nullable FK to ``users.id`` with
ON DELETE CASCADE, indexed for the "list my activity" access path. The
``created_at`` column is indexed because activity is always read newest-first.

The reference to the concerned entity (``entity_type`` + ``entity_id``) is
deliberately NOT a foreign key: activity outlives the rows it records, so a
cascade would defeat the ledger's purpose. ``payload`` is ``JSONB`` on
Postgres. Net-new DDL, not a freeze exception.

Revision ID: 0008_activity_domain
Revises: 0007_task_domain
Create Date: 2026-08-15
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_activity_domain"
down_revision = "0007_task_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "activities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_activities_user_id"), "activities", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_activities_created_at"),
        "activities",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_activities_created_at"), table_name="activities")
    op.drop_index(op.f("ix_activities_user_id"), table_name="activities")
    op.drop_table("activities")
