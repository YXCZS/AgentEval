"""Add a stable user-facing name to Experiment definitions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4d5e6f708192"
down_revision: str | Sequence[str] | None = "3c4d5e6f7081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("evaluation_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "name",
                sa.String(length=200),
                nullable=False,
                server_default="Historical Experiment",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("evaluation_runs") as batch_op:
        batch_op.drop_column("name")
