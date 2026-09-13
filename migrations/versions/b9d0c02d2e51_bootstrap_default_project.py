"""Migrate the single-workspace project identifier to default-project.

Revision ID: b9d0c02d2e51
Revises: ffe07a933165
Create Date: 2026-09-09 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9d0c02d2e51"
down_revision: Union[str, Sequence[str], None] = "ffe07a933165"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_PROJECT_ID = "project-1"
DEFAULT_PROJECT_ID = "default-project"
PROJECT_SCOPED_TABLES = (
    "api_keys",
    "agents",
    "datasets",
    "evaluator_versions",
    "evaluation_runs",
    "traces",
    "annotation_queues",
    "human_score_audits",
)


def upgrade() -> None:
    bind = op.get_bind()
    has_legacy_project = bind.execute(
        sa.text("SELECT 1 FROM projects WHERE id = :project_id"),
        {"project_id": LEGACY_PROJECT_ID},
    ).scalar_one_or_none()
    has_default_project = bind.execute(
        sa.text("SELECT 1 FROM projects WHERE id = :project_id"),
        {"project_id": DEFAULT_PROJECT_ID},
    ).scalar_one_or_none()

    if has_legacy_project is None or has_default_project is not None:
        return

    legacy_project = bind.execute(
        sa.text("SELECT name, created_at FROM projects WHERE id = :project_id"),
        {"project_id": LEGACY_PROJECT_ID},
    ).mappings().one()
    parameters = {
        "legacy_project_id": LEGACY_PROJECT_ID,
        "default_project_id": DEFAULT_PROJECT_ID,
    }
    bind.execute(
        sa.text(
            "INSERT INTO projects (id, name, created_at) "
            "VALUES (:default_project_id, :name, :created_at)"
        ),
        {**parameters, "name": legacy_project["name"], "created_at": legacy_project["created_at"]},
    )
    for table_name in PROJECT_SCOPED_TABLES:
        bind.execute(
            sa.text(
                f"UPDATE {table_name} SET project_id = :default_project_id "
                "WHERE project_id = :legacy_project_id"
            ),
            parameters,
        )
    bind.execute(
        sa.text("DELETE FROM projects WHERE id = :legacy_project_id"),
        parameters,
    )


def downgrade() -> None:
    raise NotImplementedError("Restore a pre-migration backup instead of downgrading project data.")
