"""Store the optional baseline reference for immutable experiment definitions."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7e8f9a0b123"
down_revision: Union[str, Sequence[str], None] = "c4e6f7a8b901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("evaluation_runs") as batch_op:
        batch_op.add_column(sa.Column("baseline_run_id", sa.String(length=128), nullable=True))
        batch_op.create_foreign_key(
            "fk_evaluation_runs_baseline_run",
            "evaluation_runs",
            ["baseline_run_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("evaluation_runs") as batch_op:
        batch_op.drop_constraint("fk_evaluation_runs_baseline_run", type_="foreignkey")
        batch_op.drop_column("baseline_run_id")
