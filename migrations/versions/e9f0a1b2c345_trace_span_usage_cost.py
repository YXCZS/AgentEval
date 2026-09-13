"""Store token usage and cost on canonical trace spans."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9f0a1b2c345"
down_revision: str | Sequence[str] | None = "d7e8f9a0b123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add usage as nullable first so existing installations can be backfilled
    # before the canonical column becomes required for newly written spans.
    with op.batch_alter_table("trace_spans") as batch_op:
        batch_op.add_column(sa.Column("usage", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("cost", sa.JSON(), nullable=True))

    op.execute(sa.text("UPDATE trace_spans SET usage = '{}' WHERE usage IS NULL"))

    with op.batch_alter_table("trace_spans") as batch_op:
        batch_op.alter_column("usage", existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("trace_spans") as batch_op:
        batch_op.drop_column("cost")
        batch_op.drop_column("usage")
