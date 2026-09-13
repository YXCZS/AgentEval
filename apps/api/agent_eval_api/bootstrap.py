"""Idempotent bootstrap for the single-workspace local Compose environment."""

from collections.abc import Callable

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from agent_eval_api.db import (
    AgentRecord,
    AnnotationQueueRecord,
    ApiKeyRecord,
    DatasetRecord,
    EvaluationRunRecord,
    EvaluatorVersionRecord,
    HumanScoreAuditRecord,
    ProjectRecord,
    ProviderConnectionRecord,
    TraceRecord,
    get_session_factory,
)
from agent_eval_api.settings import get_settings

DEFAULT_PROJECT_ID = "default-project"
LEGACY_PROJECT_ID = "project-1"
PROJECT_SCOPED_MODELS = (
    ApiKeyRecord,
    AgentRecord,
    DatasetRecord,
    EvaluatorVersionRecord,
    EvaluationRunRecord,
    TraceRecord,
    AnnotationQueueRecord,
    HumanScoreAuditRecord,
    ProviderConnectionRecord,
)


def migrate_legacy_project(session: Session) -> bool:
    """Move the former single-project namespace without changing record identities."""

    legacy_project = session.get(ProjectRecord, LEGACY_PROJECT_ID)
    if legacy_project is None:
        return False
    if session.get(ProjectRecord, DEFAULT_PROJECT_ID) is not None:
        return False

    # A separate destination row keeps foreign-key checks valid on PostgreSQL
    # while the direct project references move to the new namespace.
    session.add(
        ProjectRecord(
            id=DEFAULT_PROJECT_ID,
            name=legacy_project.name,
            created_at=legacy_project.created_at,
        )
    )
    session.flush()
    for model in PROJECT_SCOPED_MODELS:
        session.execute(
            update(model)
            .where(model.project_id == LEGACY_PROJECT_ID)
            .values(project_id=DEFAULT_PROJECT_ID)
        )
    session.execute(delete(ProjectRecord).where(ProjectRecord.id == LEGACY_PROJECT_ID))
    return True


def ensure_default_project(session_factory: Callable[[], Session] | None = None) -> None:
    session = (session_factory or get_session_factory())()
    try:
        migrated = migrate_legacy_project(session)
        if session.get(ProjectRecord, DEFAULT_PROJECT_ID) is None:
            session.add(ProjectRecord(id=DEFAULT_PROJECT_ID, name="Default project"))
        if migrated or session.new:
            session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    get_settings()
    ensure_default_project()
