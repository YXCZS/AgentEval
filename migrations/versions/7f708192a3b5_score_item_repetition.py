"""Associate scores with Experiment Item repetitions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7f708192a3b5"
down_revision: str | Sequence[str] | None = "6f708192a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("scores") as batch_op:
        batch_op.drop_constraint("uq_scores_case_metric_source", type_="unique")
        batch_op.add_column(sa.Column("experiment_item_id", sa.String(length=128)))
        batch_op.add_column(
            sa.Column("repetition", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("attempt", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_scores_experiment_item_id",
            "experiment_item_attempts",
            ["experiment_item_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_check_constraint("ck_scores_repetition_positive", "repetition >= 1")
        batch_op.create_check_constraint(
            "ck_scores_attempt_positive", "attempt IS NULL OR attempt >= 1"
        )
        batch_op.create_unique_constraint(
            "uq_scores_case_metric_source",
            [
                "run_id",
                "case_id",
                "repetition",
                "metric_name",
                "evaluator_version_id",
                "source",
            ],
        )


def downgrade() -> None:
    with op.batch_alter_table("scores") as batch_op:
        batch_op.drop_constraint("uq_scores_case_metric_source", type_="unique")
        batch_op.drop_constraint("ck_scores_attempt_positive", type_="check")
        batch_op.drop_constraint("ck_scores_repetition_positive", type_="check")
        batch_op.drop_constraint("fk_scores_experiment_item_id", type_="foreignkey")
        batch_op.drop_column("attempt")
        batch_op.drop_column("repetition")
        batch_op.drop_column("experiment_item_id")
        batch_op.create_unique_constraint(
            "uq_scores_case_metric_source",
            ["run_id", "case_id", "metric_name", "evaluator_version_id", "source"],
        )
