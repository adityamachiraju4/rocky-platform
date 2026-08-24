"""native lists domain

Revision ID: 0013_lists_domain
Revises: 0012_notes_domain
Create Date: 2026-08-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013_lists_domain"
down_revision = "0012_notes_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lists",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("status", sa.String(32), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_lists_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lists_user_status", "lists", ["user_id", "status"])
    op.create_index("ix_lists_user_updated", "lists", ["user_id", "updated_at"])
    op.create_index(op.f("ix_lists_user_id"), "lists", ["user_id"])
    op.create_table(
        "list_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("list_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), server_default="active", nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('active', 'complete')", name="ck_list_items_status"),
        sa.CheckConstraint("position >= 0", name="ck_list_items_position"),
        sa.ForeignKeyConstraint(["list_id"], ["lists.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("list_id", "position", name="uq_list_items_list_position"),
    )
    op.create_index("ix_list_items_list_status", "list_items", ["list_id", "status"])
    op.create_index("ix_list_items_list_position", "list_items", ["list_id", "position"])
    op.create_index(op.f("ix_list_items_list_id"), "list_items", ["list_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_list_items_list_id"), table_name="list_items")
    op.drop_index("ix_list_items_list_position", table_name="list_items")
    op.drop_index("ix_list_items_list_status", table_name="list_items")
    op.drop_table("list_items")
    op.drop_index(op.f("ix_lists_user_id"), table_name="lists")
    op.drop_index("ix_lists_user_updated", table_name="lists")
    op.drop_index("ix_lists_user_status", table_name="lists")
    op.drop_table("lists")
