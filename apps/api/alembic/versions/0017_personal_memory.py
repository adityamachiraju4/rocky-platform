"""Explicit durable personal memory.

Revision ID: 0017_personal_memory
Revises: 0016_conversation_pending_plans
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_personal_memory"
down_revision = "0016_conversation_pending_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "personal_memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("source", sa.String(32), server_default="explicit_user", nullable=False),
        sa.Column("source_thread_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("forgotten_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('fact', 'preference')", name="ck_personal_memories_kind"),
        sa.CheckConstraint("status IN ('active', 'forgotten')", name="ck_personal_memories_status"),
        sa.CheckConstraint("source = 'explicit_user'", name="ck_personal_memories_source"),
        sa.CheckConstraint("length(subject) BETWEEN 1 AND 255 AND length(trim(subject)) > 0", name="ck_personal_memories_subject"),
        sa.CheckConstraint("length(content) BETWEEN 1 AND 2000 AND length(trim(content)) > 0", name="ck_personal_memories_content"),
        sa.CheckConstraint("(status = 'active' AND forgotten_at IS NULL) OR (status = 'forgotten' AND forgotten_at IS NOT NULL)", name="ck_personal_memories_lifecycle"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_thread_id"], ["conversation_threads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_personal_memories_user_id", "personal_memories", ["user_id"])
    op.create_index("ix_personal_memories_user_status", "personal_memories", ["user_id", "status"])
    op.create_index("ix_personal_memories_user_updated", "personal_memories", ["user_id", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_personal_memories_user_updated", table_name="personal_memories")
    op.drop_index("ix_personal_memories_user_status", table_name="personal_memories")
    op.drop_index("ix_personal_memories_user_id", table_name="personal_memories")
    op.drop_table("personal_memories")
