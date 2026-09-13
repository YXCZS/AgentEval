"""Add project-scoped public Trace IDs for idempotent ingestion."""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: str | Sequence[str] | None = "e9f0a1b2c345"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("traces") as batch_op:
        batch_op.add_column(sa.Column("trace_id", sa.String(length=128), nullable=True))

    # Existing installations used the internal primary key as the only trace
    # identifier. Preserve lookup behavior before adding the new uniqueness rule.
    op.execute(sa.text("UPDATE traces SET trace_id = id WHERE trace_id IS NULL"))

    with op.batch_alter_table("traces") as batch_op:
        batch_op.create_unique_constraint(
            "uq_traces_project_source_trace", ["project_id", "source", "trace_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("traces") as batch_op:
        batch_op.drop_constraint("uq_traces_project_source_trace", type_="unique")
        batch_op.drop_column("trace_id")
