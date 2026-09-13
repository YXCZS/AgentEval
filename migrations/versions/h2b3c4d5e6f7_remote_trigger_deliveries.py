"""Persist remote trigger delivery state and attempts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = (
    "g1a2b3c4d5e6",
    "9b92a3b4c5d7",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "remote_trigger_deliveries",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("trigger_id", sa.String(length=128), nullable=False),
        sa.Column("experiment_id", sa.String(length=128), nullable=False),
        sa.Column("delivery_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("last_error_type", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.String(length=200), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'delivering', 'accepted', 'rejected', 'failed')",
            name="ck_remote_trigger_delivery_status",
        ),
        sa.ForeignKeyConstraint(["trigger_id"], ["remote_triggers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["experiment_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_id", name="uq_remote_trigger_deliveries_delivery_id"),
        sa.UniqueConstraint("experiment_id", name="uq_remote_trigger_deliveries_experiment"),
    )
    op.create_index(
        "ix_remote_trigger_deliveries_trigger_created",
        "remote_trigger_deliveries",
        ["trigger_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_remote_trigger_deliveries_trigger_created",
        table_name="remote_trigger_deliveries",
    )
    op.drop_table("remote_trigger_deliveries")
