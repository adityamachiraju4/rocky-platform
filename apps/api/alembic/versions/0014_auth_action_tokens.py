"""single-use auth action tokens

Revision ID: 0014_auth_action_tokens
Revises: 0013_lists_domain
Create Date: 2026-09-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_auth_action_tokens"
down_revision = "0013_lists_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_action_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("purpose IN ('verify_email', 'reset_password')", name="ck_auth_action_tokens_purpose"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_auth_action_tokens_token_hash"), "auth_action_tokens", ["token_hash"], unique=True)
    op.create_index(op.f("ix_auth_action_tokens_user_id"), "auth_action_tokens", ["user_id"])
    op.create_index("ix_auth_action_tokens_user_purpose", "auth_action_tokens", ["user_id", "purpose"])


def downgrade() -> None:
    op.drop_index("ix_auth_action_tokens_user_purpose", table_name="auth_action_tokens")
    op.drop_index(op.f("ix_auth_action_tokens_user_id"), table_name="auth_action_tokens")
    op.drop_index(op.f("ix_auth_action_tokens_token_hash"), table_name="auth_action_tokens")
    op.drop_table("auth_action_tokens")
