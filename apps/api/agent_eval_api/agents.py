"""Endpoint-independent Agent Releases and one-way legacy HTTP migration."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent_eval_api.auth import AuthContext, get_db, require_project_access
from agent_eval_api.contracts import (
    AgentReleaseRegistrationRequest,
    AgentReleaseResponse,
    AgentType,
    LegacyHttpAgentMigrationRequest,
    LegacyHttpAgentMigrationResponse,
    RemoteTriggerCreated,
)
from agent_eval_api.db import AgentVersionRecord, DatasetRecord, ProjectRecord, new_id
from agent_eval_api.remote_triggers import create_remote_trigger_record, public_trigger
from agent_eval_api.settings import Settings, get_settings

releases_router = APIRouter(
    prefix="/projects/{project_id}/agent-releases",
    tags=["agent-releases"],
)
migrations_router = APIRouter(
    prefix="/projects/{project_id}/legacy-http-agent-migrations",
    tags=["legacy-migrations"],
)


def get_project(db: Session, project_id: str) -> ProjectRecord:
    project = db.get(ProjectRecord, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return project


def release_response(release: AgentVersionRecord) -> AgentReleaseResponse:
    """Never expose historical transport configuration through Release APIs."""

    return AgentReleaseResponse(
        id=release.id,
        project_id=release.project_id,
        version=release.version,
        label=release.label,
        agent_type=AgentType(release.agent_type),
        release_identity=release.release_identity,
        source_revision=release.source_revision,
        metadata=release.metadata_json,
        enabled=release.enabled,
        created_at=release.created_at,
    )


def _require_browser(auth: AuthContext) -> None:
    if auth.principal_type != "browser":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="browser session required to migrate legacy HTTP Agent configurations",
        )


def _next_endpoint_independent_version(db: Session, project_id: str) -> int:
    latest = db.scalar(
        select(func.max(AgentVersionRecord.version)).where(
            AgentVersionRecord.project_id == project_id,
            AgentVersionRecord.agent_id.is_(None),
        )
    )
    return (latest or 0) + 1


@releases_router.post(
    "",
    response_model=AgentReleaseResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_agent_release(
    project_id: str,
    payload: AgentReleaseRegistrationRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> AgentReleaseResponse:
    """Register a release identity for SDK, OTel, Remote Upload, or Trigger."""

    get_project(db, project_id)
    release = AgentVersionRecord(
        id=new_id(),
        project_id=project_id,
        agent_id=None,
        version=_next_endpoint_independent_version(db, project_id),
        label=payload.label,
        agent_type=payload.agent_type.value,
        release_identity=payload.release_identity,
        source_revision=payload.source_revision,
        metadata_json=payload.metadata,
        endpoint_config=None,
    )
    db.add(release)
    db.commit()
    db.refresh(release)
    return release_response(release)


@releases_router.get("", response_model=list[AgentReleaseResponse])
def list_project_agent_releases(
    project_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> list[AgentReleaseResponse]:
    get_project(db, project_id)
    releases = db.scalars(
        select(AgentVersionRecord)
        .where(
            AgentVersionRecord.project_id == project_id,
            AgentVersionRecord.agent_id.is_(None),
        )
        .order_by(AgentVersionRecord.created_at.desc())
    ).all()
    # SQLite JSON stores Python ``None`` as JSON ``null``, which does not
    # satisfy SQL ``IS NULL``. Filter after decoding to keep both backends aligned.
    return [release_response(release) for release in releases if release.endpoint_config is None]


@migrations_router.post(
    "",
    response_model=LegacyHttpAgentMigrationResponse,
    status_code=status.HTTP_201_CREATED,
)
def migrate_legacy_http_agent_release(
    project_id: str,
    payload: LegacyHttpAgentMigrationRequest,
    db: Session = Depends(get_db),  # noqa: B008
    auth: AuthContext = Depends(require_project_access),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> LegacyHttpAgentMigrationResponse:
    """Create a supported release without copying a historical endpoint or auth ref."""

    _require_browser(auth)
    get_project(db, project_id)
    legacy = db.scalar(
        select(AgentVersionRecord).where(
            AgentVersionRecord.id == payload.legacy_agent_version_id,
            AgentVersionRecord.project_id == project_id,
            AgentVersionRecord.agent_id.is_not(None),
            AgentVersionRecord.endpoint_config.is_not(None),
        )
    )
    if legacy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="legacy HTTP Agent release not found",
        )
    dataset = db.scalar(
        select(DatasetRecord).where(
            DatasetRecord.id == payload.dataset_id,
            DatasetRecord.project_id == project_id,
        )
    )
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dataset not found")

    trigger_created: RemoteTriggerCreated | None = None
    if payload.target_mode == "remote_trigger":
        # The helper checks for an existing trigger before this transaction adds a Release.
        trigger, signing_secret = create_remote_trigger_record(
            db=db,
            settings=settings,
            project_id=project_id,
            dataset_id=dataset.id,
            trigger_url=str(payload.trigger_url),
        )
        trigger_created = RemoteTriggerCreated(
            **public_trigger(trigger).model_dump(),
            signing_secret=signing_secret,
        )

    release = AgentVersionRecord(
        id=new_id(),
        project_id=project_id,
        agent_id=None,
        version=_next_endpoint_independent_version(db, project_id),
        label=_migrated_label(legacy.label),
        agent_type=legacy.agent_type,
        release_identity=legacy.release_identity,
        source_revision=legacy.source_revision,
        metadata_json={
            "migration": {
                "source_legacy_agent_version_id": legacy.id,
                "target_mode": payload.target_mode,
            }
        },
        endpoint_config=None,
        enabled=legacy.enabled,
    )
    db.add(release)
    db.commit()
    db.refresh(release)
    if trigger_created is not None:
        db.refresh(trigger)
        trigger_created = RemoteTriggerCreated(
            **public_trigger(trigger).model_dump(),
            signing_secret=trigger_created.signing_secret,
        )
    return LegacyHttpAgentMigrationResponse(
        release=release_response(release),
        trigger=trigger_created,
    )


def _migrated_label(label: str) -> str:
    """Preserve the migration marker without violating the public label limit."""

    suffix = " (migrated)"
    return f"{label[: 100 - len(suffix)]}{suffix}"
