"""User-managed external LLM Judge connection references."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_eval_api.auth import AuthContext, get_db, require_project_access
from agent_eval_api.contracts import (
    EvaluatorConnection,
    EvaluatorConnectionCreateRequest,
)
from agent_eval_api.db import EvaluatorConnectionRecord, ProjectRecord, new_id

router = APIRouter(
    prefix="/projects/{project_id}/evaluator-connections",
    tags=["evaluator-connections"],
)


def get_project(db: Session, project_id: str) -> ProjectRecord:
    project = db.get(ProjectRecord, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return project


def response(record: EvaluatorConnectionRecord) -> EvaluatorConnection:
    return EvaluatorConnection.model_validate(
        {
            "id": record.id,
            "project_id": record.project_id,
            "name": record.name,
            "endpoint": record.endpoint,
            "auth_ref": record.auth_ref,
            "timeout_seconds": record.timeout_seconds,
            "enabled": record.enabled,
            "created_at": record.created_at,
        }
    )


@router.post("", response_model=EvaluatorConnection, status_code=status.HTTP_201_CREATED)
def create_evaluator_connection(
    project_id: str,
    payload: EvaluatorConnectionCreateRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> EvaluatorConnection:
    get_project(db, project_id)
    record = EvaluatorConnectionRecord(
        id=new_id(),
        project_id=project_id,
        name=payload.name,
        endpoint=str(payload.endpoint),
        auth_ref=payload.auth_ref,
        timeout_seconds=payload.timeout_seconds,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return response(record)


@router.get("", response_model=list[EvaluatorConnection])
def list_evaluator_connections(
    project_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> list[EvaluatorConnection]:
    get_project(db, project_id)
    records = db.scalars(
        select(EvaluatorConnectionRecord)
        .where(EvaluatorConnectionRecord.project_id == project_id)
        .order_by(EvaluatorConnectionRecord.created_at.desc())
    ).all()
    return [response(record) for record in records]


@router.get("/{connection_id}", response_model=EvaluatorConnection)
def read_evaluator_connection(
    project_id: str,
    connection_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> EvaluatorConnection:
    record = db.scalar(
        select(EvaluatorConnectionRecord).where(
            EvaluatorConnectionRecord.id == connection_id,
            EvaluatorConnectionRecord.project_id == project_id,
        )
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="evaluator connection not found",
        )
    return response(record)


@router.patch("/{connection_id}/enabled", response_model=EvaluatorConnection)
def set_evaluator_connection_enabled(
    project_id: str,
    connection_id: str,
    enabled: bool,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> EvaluatorConnection:
    record = db.scalar(
        select(EvaluatorConnectionRecord).where(
            EvaluatorConnectionRecord.id == connection_id,
            EvaluatorConnectionRecord.project_id == project_id,
        )
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="evaluator connection not found",
        )
    record.enabled = enabled
    db.commit()
    db.refresh(record)
    return response(record)
