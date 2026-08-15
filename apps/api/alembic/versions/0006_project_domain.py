"""project domain: projects

Creates the Projects capability table. Ownership is structural: a
non-nullable FK to ``users.id`` with ON DELETE CASCADE, indexed for the
"list my projects" access path. Net-new DDL, not a freeze exception.

Revision ID: 0006_project_domain
Revises: 0005_session_domain
Create Date: 2026-08-15
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_project_domain"
down_revision = "0005_session_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="active",
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_projects_user_id"), "projects", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_projects_user_id"), table_name="projects")
    op.drop_table("projects")
