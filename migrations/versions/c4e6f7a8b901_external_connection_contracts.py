"""Add external Agent release and evaluator connection contract storage."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e6f7a8b901"
down_revision: Union[str, Sequence[str], None] = "b9d0c02d2e51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "evaluator_connections",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("endpoint", sa.String(length=500), nullable=False),
        sa.Column("auth_ref", sa.String(length=200), nullable=True),
        sa.Column("timeout_seconds", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evaluator_connections_project",
        "evaluator_connections",
        ["project_id"],
        unique=False,
    )
    op.add_column("agent_versions", sa.Column("release_identity", sa.String(length=200)))
    with op.batch_alter_table("evaluator_versions") as batch_op:
        batch_op.add_column(sa.Column("evaluator_connection_id", sa.String(length=128)))
        batch_op.create_foreign_key(
            "fk_evaluator_versions_connection",
            "evaluator_connections",
            ["evaluator_connection_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("evaluator_versions") as batch_op:
        batch_op.drop_constraint("fk_evaluator_versions_connection", type_="foreignkey")
        batch_op.drop_column("evaluator_connection_id")
    op.drop_column("agent_versions", "release_identity")
    op.drop_index("ix_evaluator_connections_project", table_name="evaluator_connections")
    op.drop_table("evaluator_connections")
