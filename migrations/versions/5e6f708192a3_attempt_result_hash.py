"""Add canonical terminal submission hashes to Experiment attempts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5e6f708192a3"
down_revision: str | Sequence[str] | None = "4d5e6f708192"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("experiment_item_attempts") as batch_op:
        batch_op.add_column(sa.Column("result_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("experiment_item_attempts") as batch_op:
        batch_op.drop_column("result_hash")
