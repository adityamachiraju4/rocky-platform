"""durable pending conversation plans

Revision ID: 0016_conversation_pending_plans
Revises: 0015_conversation_context
Create Date: 2026-09-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016_conversation_pending_plans"
down_revision = "0015_conversation_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_pending_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("plan_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'executing', 'completed', 'failed', 'rejected', 'expired')",
            name="ck_conversation_pending_plans_status",
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["conversation_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_conversation_pending_plans_thread_id"), "conversation_pending_plans", ["thread_id"])
    op.create_index(op.f("ix_conversation_pending_plans_user_id"), "conversation_pending_plans", ["user_id"])
    op.create_index(
        "ix_conversation_pending_plans_owner_thread_status",
        "conversation_pending_plans", ["user_id", "thread_id", "status"],
    )
    op.create_index(
        "uq_conversation_pending_plans_owner_thread_pending",
        "conversation_pending_plans",
        ["user_id", "thread_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_conversation_pending_plans_owner_thread_pending",
        table_name="conversation_pending_plans",
    )
    op.drop_index("ix_conversation_pending_plans_owner_thread_status", table_name="conversation_pending_plans")
    op.drop_index(op.f("ix_conversation_pending_plans_user_id"), table_name="conversation_pending_plans")
    op.drop_index(op.f("ix_conversation_pending_plans_thread_id"), table_name="conversation_pending_plans")
    op.drop_table("conversation_pending_plans")
