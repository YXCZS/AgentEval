"""Browser-managed credentials for SDK and CI clients."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_eval_api.auth import (
    AuthContext,
    get_db,
    issue_project_key,
    require_project_access,
)
from agent_eval_api.contracts import (
    ProjectApiKey,
    ProjectApiKeyCreated,
    ProjectApiKeyCreateRequest,
)
from agent_eval_api.db import ApiKeyRecord
from agent_eval_api.settings import Settings, get_settings

router = APIRouter(prefix="/projects/{project_id}/api-keys", tags=["auth"])


def require_browser(auth: AuthContext) -> None:
    if auth.principal_type != "browser":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="browser session required to manage project keys",
        )


def key_response(record: ApiKeyRecord) -> ProjectApiKey:
    return ProjectApiKey(
        id=record.id,
        name=record.name,
        key_prefix=record.key_prefix,
        active=record.active,
        created_at=record.created_at,
        last_used_at=record.last_used_at,
    )


@router.get("", response_model=list[ProjectApiKey])
def list_project_keys(
    project_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    auth: AuthContext = Depends(require_project_access),  # noqa: B008
) -> list[ProjectApiKey]:
    require_browser(auth)
    records = db.scalars(
        select(ApiKeyRecord)
        .where(ApiKeyRecord.project_id == project_id)
        .order_by(ApiKeyRecord.created_at.desc())
    ).all()
    return [key_response(record) for record in records]


@router.post("", response_model=ProjectApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_project_key(
    project_id: str,
    payload: ProjectApiKeyCreateRequest,
    db: Session = Depends(get_db),  # noqa: B008
    auth: AuthContext = Depends(require_project_access),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ProjectApiKeyCreated:
    require_browser(auth)
    raw_key, record = issue_project_key(project_id, settings, name=payload.name)
    db.add(record)
    db.commit()
    db.refresh(record)
    return ProjectApiKeyCreated(**key_response(record).model_dump(), key=raw_key)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_project_key(
    project_id: str,
    key_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    auth: AuthContext = Depends(require_project_access),  # noqa: B008
) -> Response:
    require_browser(auth)
    record = db.scalar(
        select(ApiKeyRecord).where(
            ApiKeyRecord.id == key_id,
            ApiKeyRecord.project_id == project_id,
        )
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project key not found")
    record.active = False
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
