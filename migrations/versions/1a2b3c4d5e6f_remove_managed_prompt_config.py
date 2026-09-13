"""Remove the platform-managed Prompt Agent configuration column."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "1a2b3c4d5e6f"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Legacy prompt configurations were backed up and rehearsed before this
    # breaking migration. Runtime Agent releases now use endpoint_config only.
    with op.batch_alter_table("agent_versions") as batch_op:
        batch_op.drop_column("prompt_config")


def downgrade() -> None:
    with op.batch_alter_table("agent_versions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "prompt_config",
                sa.JSON().with_variant(
                    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
                ),
                nullable=True,
            )
        )
