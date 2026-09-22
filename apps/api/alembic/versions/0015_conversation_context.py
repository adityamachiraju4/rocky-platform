"""durable conversation context

Revision ID: 0015_conversation_context
Revises: 0014_auth_action_tokens
Create Date: 2026-09-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015_conversation_context"
down_revision = "0014_auth_action_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("default_key", sa.String(16)),
        sa.Column("title", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("default_key IS NULL OR default_key = 'default'", name="ck_conversation_threads_default_key"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "default_key", name="uq_conversation_threads_user_default"),
    )
    op.create_index(op.f("ix_conversation_threads_user_id"), "conversation_threads", ["user_id"])
    op.create_index("ix_conversation_threads_user_updated", "conversation_threads", ["user_id", "updated_at"])

    op.create_table(
        "conversation_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("language", sa.String(35)),
        sa.Column("turn_metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_conversation_turns_role"),
        sa.ForeignKeyConstraint(["thread_id"], ["conversation_threads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_conversation_turns_thread_id"), "conversation_turns", ["thread_id"])
    op.create_index("ix_conversation_turns_thread_created", "conversation_turns", ["thread_id", "created_at"])

    op.create_table(
        "conversation_grounded_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True)),
        sa.Column("display_text", sa.String(500), nullable=False),
        sa.Column("reference_metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["conversation_threads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", "kind", name="uq_conversation_grounded_thread_kind"),
    )
    op.create_index(op.f("ix_conversation_grounded_references_thread_id"), "conversation_grounded_references", ["thread_id"])
    op.create_index("ix_conversation_grounded_thread_updated", "conversation_grounded_references", ["thread_id", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_conversation_grounded_thread_updated", table_name="conversation_grounded_references")
    op.drop_index(op.f("ix_conversation_grounded_references_thread_id"), table_name="conversation_grounded_references")
    op.drop_table("conversation_grounded_references")
    op.drop_index("ix_conversation_turns_thread_created", table_name="conversation_turns")
    op.drop_index(op.f("ix_conversation_turns_thread_id"), table_name="conversation_turns")
    op.drop_table("conversation_turns")
    op.drop_index("ix_conversation_threads_user_updated", table_name="conversation_threads")
    op.drop_index(op.f("ix_conversation_threads_user_id"), table_name="conversation_threads")
    op.drop_table("conversation_threads")
