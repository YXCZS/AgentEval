"""Add versioned platform-managed Judge configuration."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b92a3b4c5d7"
down_revision: str | Sequence[str] | None = "8a8192a3b4c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("evaluator_versions") as batch_op:
        batch_op.add_column(sa.Column("provider_connection_id", sa.String(length=128)))
        batch_op.add_column(sa.Column("prompt_template", sa.Text()))
        batch_op.add_column(sa.Column("output_schema", sa.JSON()))
        batch_op.add_column(sa.Column("sampling_parameters", sa.JSON()))
        batch_op.create_foreign_key(
            "fk_evaluator_versions_provider_connection_id",
            "provider_connections",
            ["provider_connection_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("evaluator_versions") as batch_op:
        batch_op.drop_constraint(
            "fk_evaluator_versions_provider_connection_id", type_="foreignkey"
        )
        batch_op.drop_column("sampling_parameters")
        batch_op.drop_column("output_schema")
        batch_op.drop_column("prompt_template")
        batch_op.drop_column("provider_connection_id")
