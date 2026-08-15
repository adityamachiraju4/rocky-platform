"""add client_id to devices with (user_id, client_id) uniqueness

Introduces a client-generated opaque stable identifier for devices, enabling
device continuity across logins. The column is nullable: clients that do not
supply a client_id still authenticate (a new device row is created without
one). Uniqueness on (user_id, client_id) makes a supplied client_id resolve
to exactly one logical device per user.

This is an approved additive freeze exception to the Identity `devices` table
(new nullable column + named unique constraint; no data rewrite).

Revision ID: 0004_device_client_id
Revises: 0003_user_session_version
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_device_client_id"
down_revision: Union[str, None] = "0003_user_session_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_devices_user_id_client_id",
        "devices",
        ["user_id", "client_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_devices_user_id_client_id",
        "devices",
        type_="unique",
    )
    op.drop_column("devices", "client_id")
