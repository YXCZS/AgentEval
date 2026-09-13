"""Add Dataset-scoped remote trigger configuration."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "g1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "remote_triggers",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("dataset_id", sa.String(length=128), nullable=False),
        sa.Column("trigger_url", sa.String(length=500), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "signature_header",
            sa.String(length=100),
            nullable=False,
            server_default="X-Agent-Eval-Trigger-Signature",
        ),
        sa.Column("secret_mask", sa.String(length=64), nullable=False),
        sa.Column("secret_key_id", sa.String(length=128), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("secret_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", name="uq_remote_triggers_dataset"),
    )
    op.create_index(
        "ix_remote_triggers_project_enabled",
        "remote_triggers",
        ["project_id", "enabled"],
    )


def downgrade() -> None:
    op.drop_index("ix_remote_triggers_project_enabled", table_name="remote_triggers")
    op.drop_table("remote_triggers")
