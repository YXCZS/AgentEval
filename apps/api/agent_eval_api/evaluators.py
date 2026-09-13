"""Project-scoped registration for immutable evaluator versions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent_eval_api.auth import AuthContext, get_db, require_project_access
from agent_eval_api.contracts import (
    AgentType,
    EvaluatorType,
    EvaluatorVersion,
    EvaluatorVersionCreateRequest,
    JudgeSamplingParameters,
    ScoreDirection,
)
from agent_eval_api.db import (
    EvaluatorConnectionRecord,
    EvaluatorVersionRecord,
    ProjectRecord,
    ProviderConnectionRecord,
    new_id,
)

router = APIRouter(prefix="/projects/{project_id}/evaluators", tags=["evaluators"])


def get_project(db: Session, project_id: str) -> ProjectRecord:
    project = db.get(ProjectRecord, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return project


def get_evaluator(
    db: Session,
    project_id: str,
    evaluator_id: str,
) -> EvaluatorVersionRecord:
    evaluator = db.scalar(
        select(EvaluatorVersionRecord).where(
            EvaluatorVersionRecord.id == evaluator_id,
            EvaluatorVersionRecord.project_id == project_id,
        )
    )
    if evaluator is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="evaluator not found")
    return evaluator


def evaluator_response(record: EvaluatorVersionRecord) -> EvaluatorVersion:
    return EvaluatorVersion(
        id=record.id,
        name=record.name,
        version=record.version,
        evaluator_type=EvaluatorType(record.evaluator_type),
        requires=record.requires,
        supported_agent_types=[
            AgentType(agent_type) for agent_type in record.supported_agent_types
        ],
        score_min=record.score_min,
        score_max=record.score_max,
        direction=ScoreDirection(record.direction),
        default_threshold=record.default_threshold,
        rubric=record.rubric,
        evaluator_connection_id=record.evaluator_connection_id,
        provider_connection_id=record.provider_connection_id,
        judge_model=record.judge_model,
        prompt_template=record.prompt_template,
        output_schema=record.output_schema,
        sampling_parameters=(
            JudgeSamplingParameters.model_validate(record.sampling_parameters)
            if record.sampling_parameters is not None
            else None
        ),
        config=record.config,
        enabled=record.enabled,
    )


@router.post("", response_model=EvaluatorVersion, status_code=status.HTTP_201_CREATED)
def register_evaluator(
    project_id: str,
    payload: EvaluatorVersionCreateRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> EvaluatorVersion:
    get_project(db, project_id)
    uses_external = payload.evaluator_connection_id is not None
    uses_managed = payload.provider_connection_id is not None
    if payload.evaluator_type is EvaluatorType.LLM_JUDGE and uses_external == uses_managed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "llm_judge evaluators require exactly one provider_connection_id "
                "or evaluator_connection_id"
            ),
        )
    if payload.evaluator_type is not EvaluatorType.LLM_JUDGE and (uses_external or uses_managed):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="only llm_judge evaluators can bind a Judge connection",
        )
    if payload.evaluator_connection_id is not None:
        connection = db.scalar(
            select(EvaluatorConnectionRecord).where(
                EvaluatorConnectionRecord.id == payload.evaluator_connection_id,
                EvaluatorConnectionRecord.project_id == project_id,
                EvaluatorConnectionRecord.enabled.is_(True),
            )
        )
        if connection is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="evaluator connection not found",
            )
    provider: ProviderConnectionRecord | None = None
    if payload.provider_connection_id is not None:
        provider = db.scalar(
            select(ProviderConnectionRecord).where(
                ProviderConnectionRecord.id == payload.provider_connection_id,
                ProviderConnectionRecord.project_id == project_id,
                ProviderConnectionRecord.status == "active",
                ProviderConnectionRecord.enabled.is_(True),
            )
        )
        if provider is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="active provider connection not found",
            )
        missing = [
            name
            for name, value in (
                ("rubric", payload.rubric),
                ("judge_model", payload.judge_model),
                ("prompt_template", payload.prompt_template),
                ("output_schema", payload.output_schema),
                ("default_threshold", payload.default_threshold),
            )
            if value is None
        ]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "message": "managed llm_judge configuration is incomplete",
                    "missing_fields": missing,
                },
            )
        assert payload.output_schema is not None
        try:
            Draft202012Validator.check_schema(payload.output_schema)
        except SchemaError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="output_schema is not a valid JSON Schema",
            ) from exc
    record = EvaluatorVersionRecord(
        id=new_id(),
        project_id=project_id,
        name=payload.name,
        version=payload.version,
        evaluator_type=payload.evaluator_type.value,
        requires=payload.requires,
        supported_agent_types=[agent_type.value for agent_type in payload.supported_agent_types],
        score_min=payload.score_min,
        score_max=payload.score_max,
        direction=payload.direction.value,
        default_threshold=payload.default_threshold,
        rubric=payload.rubric,
        evaluator_connection_id=payload.evaluator_connection_id,
        provider_connection_id=payload.provider_connection_id,
        judge_model=payload.judge_model,
        prompt_template=payload.prompt_template,
        output_schema=payload.output_schema,
        sampling_parameters=(
            (payload.sampling_parameters or JudgeSamplingParameters()).model_dump(mode="json")
            if provider is not None
            else None
        ),
        config=payload.config,
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="evaluator name and version already exist",
        ) from None
    db.refresh(record)
    return evaluator_response(record)


@router.get("", response_model=list[EvaluatorVersion])
def list_evaluators(
    project_id: str,
    enabled: bool | None = None,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> list[EvaluatorVersion]:
    get_project(db, project_id)
    statement = (
        select(EvaluatorVersionRecord)
        .where(EvaluatorVersionRecord.project_id == project_id)
        .order_by(EvaluatorVersionRecord.name, EvaluatorVersionRecord.version)
    )
    if enabled is not None:
        statement = statement.where(EvaluatorVersionRecord.enabled.is_(enabled))
    return [evaluator_response(record) for record in db.scalars(statement)]


@router.get("/{evaluator_id}", response_model=EvaluatorVersion)
def read_evaluator(
    project_id: str,
    evaluator_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> EvaluatorVersion:
    return evaluator_response(get_evaluator(db, project_id, evaluator_id))


@router.patch("/{evaluator_id}/enabled", response_model=EvaluatorVersion)
def set_evaluator_enabled(
    project_id: str,
    evaluator_id: str,
    enabled: bool,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> EvaluatorVersion:
    record = get_evaluator(db, project_id, evaluator_id)
    record.enabled = enabled
    db.commit()
    db.refresh(record)
    return evaluator_response(record)
