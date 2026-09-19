"""Add soft-delete column to users so member deletion is reversible.

Revision ID: j4d5e6f7a8b9
Revises: i3c4d5e6f7a8
Create Date: 2026-09-19 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "j4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "i3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "deleted_at")
