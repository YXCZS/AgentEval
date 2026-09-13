"""Allow auditable scores to attach to online Traces and Spans."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("scores") as batch_op:
        batch_op.drop_constraint("uq_scores_case_metric", type_="unique")
        batch_op.add_column(sa.Column("span_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("source", sa.String(length=32), nullable=True))
        batch_op.alter_column(
            "run_id", existing_type=sa.String(length=128), nullable=True
        )
        batch_op.alter_column(
            "case_id", existing_type=sa.String(length=128), nullable=True
        )

    op.execute(sa.text("UPDATE scores SET source = 'automated' WHERE source IS NULL"))

    with op.batch_alter_table("scores") as batch_op:
        batch_op.alter_column(
            "source",
            existing_type=sa.String(length=32),
            nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_scores_case_metric_source",
            ["run_id", "case_id", "metric_name", "evaluator_version_id", "source"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    null_links = bind.execute(
        sa.text("SELECT COUNT(*) FROM scores WHERE run_id IS NULL OR case_id IS NULL")
    ).scalar_one()
    if null_links:
        raise RuntimeError("cannot downgrade while online Trace scores exist")

    with op.batch_alter_table("scores") as batch_op:
        batch_op.drop_constraint("uq_scores_case_metric_source", type_="unique")
        batch_op.drop_column("source")
        batch_op.drop_column("span_id")
        batch_op.alter_column(
            "run_id", existing_type=sa.String(length=128), nullable=False
        )
        batch_op.alter_column(
            "case_id", existing_type=sa.String(length=128), nullable=False
        )
        batch_op.create_unique_constraint(
            "uq_scores_case_metric",
            ["run_id", "case_id", "metric_name", "evaluator_version_id"],
        )
