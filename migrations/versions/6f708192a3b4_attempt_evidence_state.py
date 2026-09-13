"""Track public Trace correlation and server-derived evidence state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6f708192a3b4"
down_revision: str | Sequence[str] | None = "5e6f708192a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("experiment_item_attempts") as batch_op:
        batch_op.add_column(sa.Column("source_trace_id", sa.String(length=128)))
        batch_op.add_column(
            sa.Column(
                "evidence_status",
                sa.String(length=32),
                nullable=False,
                server_default="pending",
            )
        )
        batch_op.add_column(
            sa.Column("evidence_reasons", JsonType, nullable=False, server_default="[]")
        )
        batch_op.create_check_constraint(
            "ck_experiment_item_evidence_status",
            "evidence_status IN ('pending', 'complete', 'incomplete', 'not_required')",
        )


def downgrade() -> None:
    with op.batch_alter_table("experiment_item_attempts") as batch_op:
        batch_op.drop_constraint("ck_experiment_item_evidence_status", type_="check")
        batch_op.drop_column("evidence_reasons")
        batch_op.drop_column("evidence_status")
        batch_op.drop_column("source_trace_id")
