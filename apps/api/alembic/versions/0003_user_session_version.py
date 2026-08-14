"""add session_version to users (Foundation-004 Phase 2 invalidation)

Adds a user-level session_version counter. This is the approved architectural
exception for Foundation-004 Phase 2. Session.session_version is unchanged and
now represents the snapshot of the user's version at session-creation time.

Revision ID: 0003_user_session_version
Revises: 0002_identity_domain
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003_user_session_version"
down_revision: Union[str, None] = "0002_identity_domain"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "session_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "session_version")