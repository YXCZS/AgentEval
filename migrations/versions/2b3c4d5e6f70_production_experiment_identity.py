"""Add endpoint-independent Agent releases and production Experiment policies."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "2b3c4d5e6f70"
down_revision: str | Sequence[str] | None = "1a2b3c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

EXECUTION_MODES = ("sdk_task", "otel", "remote_upload", "remote_trigger")
EVIDENCE_POLICIES = (
    "trace_required",
    "llm_required",
    "tool_trajectory_required",
    "rag_trajectory_required",
)


def upgrade() -> None:
    with op.batch_alter_table("agent_versions") as batch_op:
        batch_op.add_column(sa.Column("project_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("source_revision", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("metadata", JsonType, nullable=True))

    connection = op.get_bind()
    agent_versions = sa.table(
        "agent_versions",
        sa.column("id", sa.String()),
        sa.column("agent_id", sa.String()),
        sa.column("project_id", sa.String()),
        sa.column("release_identity", sa.String()),
        sa.column("label", sa.String()),
        sa.column("metadata", JsonType),
    )
    agents = sa.table(
        "agents",
        sa.column("id", sa.String()),
        sa.column("project_id", sa.String()),
    )
    legacy_releases = connection.execute(
        sa.select(agent_versions.c.id, agents.c.project_id)
        .select_from(agent_versions.join(agents, agent_versions.c.agent_id == agents.c.id))
    ).all()
    for release_id, project_id in legacy_releases:
        connection.execute(
            agent_versions.update()
            .where(agent_versions.c.id == release_id)
            .values(project_id=project_id)
        )
    connection.execute(
        agent_versions.update()
        .where(agent_versions.c.release_identity.is_(None))
        .values(release_identity=agent_versions.c.label)
    )
    connection.execute(
        agent_versions.update()
        .where(agent_versions.c.metadata.is_(None))
        .values(metadata={})
    )

    with op.batch_alter_table("agent_versions") as batch_op:
        batch_op.alter_column("project_id", existing_type=sa.String(length=128), nullable=False)
        batch_op.alter_column(
            "release_identity",
            existing_type=sa.String(length=200),
            nullable=False,
        )
        batch_op.alter_column("metadata", existing_type=JsonType, nullable=False)
        batch_op.alter_column("agent_id", existing_type=sa.String(length=128), nullable=True)
        batch_op.create_foreign_key(
            "fk_agent_versions_project",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index(
            "ix_agent_versions_project_created",
            ["project_id", "created_at"],
            unique=False,
        )

    with op.batch_alter_table("evaluation_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "execution_mode",
                sa.String(length=32),
                nullable=False,
                server_default="remote_trigger",
            )
        )
        batch_op.add_column(
            sa.Column(
                "evidence_policy",
                sa.String(length=32),
                nullable=False,
                server_default="trace_required",
            )
        )
        batch_op.create_check_constraint(
            "ck_evaluation_runs_execution_mode",
            f"execution_mode IN {EXECUTION_MODES}",
        )
        batch_op.create_check_constraint(
            "ck_evaluation_runs_evidence_policy",
            f"evidence_policy IN {EVIDENCE_POLICIES}",
        )


def downgrade() -> None:
    connection = op.get_bind()
    standalone_release_count = connection.scalar(
        sa.text("SELECT COUNT(*) FROM agent_versions WHERE agent_id IS NULL")
    )
    if standalone_release_count:
        raise RuntimeError(
            "cannot downgrade while endpoint-independent Agent Releases exist; "
            "restore the matching pre-migration backup instead"
        )

    with op.batch_alter_table("evaluation_runs") as batch_op:
        batch_op.drop_constraint("ck_evaluation_runs_evidence_policy", type_="check")
        batch_op.drop_constraint("ck_evaluation_runs_execution_mode", type_="check")
        batch_op.drop_column("evidence_policy")
        batch_op.drop_column("execution_mode")

    with op.batch_alter_table("agent_versions") as batch_op:
        batch_op.drop_index("ix_agent_versions_project_created")
        batch_op.drop_constraint("fk_agent_versions_project", type_="foreignkey")
        batch_op.alter_column("agent_id", existing_type=sa.String(length=128), nullable=False)
        batch_op.alter_column(
            "release_identity",
            existing_type=sa.String(length=200),
            nullable=True,
        )
        batch_op.drop_column("metadata")
        batch_op.drop_column("source_revision")
        batch_op.drop_column("project_id")
