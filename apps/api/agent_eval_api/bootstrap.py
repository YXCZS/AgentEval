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
    new_id,
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


def ensure_bootstrap_admin(session_factory: Callable[[], Session] | None = None) -> None:
    """Create the initial admin user from environment variables if no user exists.

    This is a one-time provisioning step so the first administrator can log in
    and begin creating member accounts. Without it, a fresh deployment has no
    way to provision users (registration is admin-only by design).
    """
    settings = get_settings()
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        return
    session = (session_factory or get_session_factory())()
    try:
        from sqlalchemy import select

        from agent_eval_api.db import UserRecord
        from agent_eval_api.security import hash_password

        existing = session.scalar(
            select(UserRecord).where(UserRecord.email == settings.bootstrap_admin_email)
        )
        if existing is not None:
            return
        project = session.get(ProjectRecord, DEFAULT_PROJECT_ID)
        if project is None:
            project = ProjectRecord(id=DEFAULT_PROJECT_ID, name="Admin workspace")
            session.add(project)
            session.flush()
        session.add(
            UserRecord(
                id=new_id(),
                email=settings.bootstrap_admin_email,
                password_hash=hash_password(settings.bootstrap_admin_password.get_secret_value()),
                display_name="Administrator",
                role="admin",
                active=True,
                project_id=project.id,
            )
        )
        session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    get_settings()
    ensure_default_project()
    ensure_bootstrap_admin()
