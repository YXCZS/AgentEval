"""Store Trace-to-Dataset span and mapping provenance."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("dataset_cases") as batch_op:
        batch_op.add_column(sa.Column("source_span_ids", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("source_mapping", sa.JSON(), nullable=True))

    op.execute(
        sa.text("UPDATE dataset_cases SET source_span_ids = '[]' WHERE source_span_ids IS NULL")
    )
    op.execute(
        sa.text("UPDATE dataset_cases SET source_mapping = '{}' WHERE source_mapping IS NULL")
    )

    with op.batch_alter_table("dataset_cases") as batch_op:
        batch_op.alter_column("source_span_ids", existing_type=sa.JSON(), nullable=False)
        batch_op.alter_column("source_mapping", existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("dataset_cases") as batch_op:
        batch_op.drop_column("source_mapping")
        batch_op.drop_column("source_span_ids")
