"""Store immutable attempt-level evidence for externally executed Experiments."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3c4d5e6f7081"
down_revision: str | Sequence[str] | None = "2b3c4d5e6f70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "experiment_item_attempts",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("experiment_id", sa.String(length=128), nullable=False),
        sa.Column("case_id", sa.String(length=128), nullable=False),
        sa.Column("repetition", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("external_run_id", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("output", JsonType, nullable=True),
        sa.Column("usage", JsonType, nullable=False),
        sa.Column("runtime_metadata", JsonType, nullable=False),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt >= 1", name="ck_experiment_item_attempt_positive"),
        sa.CheckConstraint(
            "repetition >= 1", name="ck_experiment_item_repetition_positive"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_experiment_item_status",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["dataset_cases.id"]),
        sa.ForeignKeyConstraint(
            ["experiment_id"], ["evaluation_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["trace_id"], ["traces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment_id",
            "case_id",
            "repetition",
            "attempt",
            name="uq_experiment_item_attempt_position",
        ),
        sa.UniqueConstraint(
            "experiment_id",
            "external_run_id",
            name="uq_experiment_item_external_run",
        ),
    )
    op.create_index(
        "ix_experiment_item_status",
        "experiment_item_attempts",
        ["experiment_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_experiment_item_status", table_name="experiment_item_attempts")
    op.drop_table("experiment_item_attempts")
