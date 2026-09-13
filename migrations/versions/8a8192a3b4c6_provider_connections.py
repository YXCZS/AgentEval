"""Add encrypted platform-managed provider connection records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8a8192a3b4c6"
down_revision: str | Sequence[str] | None = "7f708192a3b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_connections",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("default_parameters", sa.JSON(), nullable=False),
        sa.Column("credential_mask", sa.String(length=64), nullable=False),
        sa.Column("credential_key_id", sa.String(length=128), nullable=False),
        sa.Column("credential_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("credential_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending_validation', 'active', 'error', 'disabled')",
            name="ck_provider_connections_status",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "name", name="uq_provider_connections_project_name"
        ),
    )
    op.create_index(
        "ix_provider_connections_project_status",
        "provider_connections",
        ["project_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_connections_project_status", table_name="provider_connections"
    )
    op.drop_table("provider_connections")
