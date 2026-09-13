"""Store external Judge provenance and raw response separately from scores."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("scores") as batch_op:
        batch_op.add_column(sa.Column("provenance", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("raw_response", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("scores") as batch_op:
        batch_op.drop_column("raw_response")
        batch_op.drop_column("provenance")
