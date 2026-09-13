"""Project-scoped human review queues, feedback Scores, and immutable audit history."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent_eval_api.auth import AuthContext, get_db, require_project_access
from agent_eval_api.contracts import (
    AnnotationQueue,
    AnnotationQueueCreateRequest,
    AnnotationQueueItem,
    AnnotationQueueItemCreateRequest,
    AnnotationStatus,
    EvaluatorType,
    ExternalScoreProvenance,
    HumanScoreAudit,
    HumanScoreRequest,
    Score,
    ScoreDirection,
    ScoreSource,
    ScoreStatus,
)
from agent_eval_api.db import (
    AnnotationQueueItemRecord,
    AnnotationQueueRecord,
    CaseExecutionRecord,
    DatasetCaseRecord,
    EvaluationRunRecord,
    EvaluatorVersionRecord,
    HumanScoreAuditRecord,
    ProjectRecord,
    ScoreRecord,
    new_id,
)
from agent_eval_api.settings import Settings, get_settings
from agent_eval_api.trace_privacy import PrivacyStats, sanitize_value

router = APIRouter(
    prefix="/projects/{project_id}/annotation-queues",
    tags=["annotation-queues"],
)


def _queue_response(record: AnnotationQueueRecord) -> AnnotationQueue:
    return AnnotationQueue(
        id=record.id,
        project_id=record.project_id,
        name=record.name,
        description=record.description,
        evaluator_version_id=record.evaluator_version_id,
        created_at=record.created_at,
    )


def _item_response(record: AnnotationQueueItemRecord) -> AnnotationQueueItem:
    return AnnotationQueueItem(
        id=record.id,
        queue_id=record.queue_id,
        run_id=record.run_id,
        case_id=record.case_id,
        trace_id=record.trace_id,
        status=AnnotationStatus(record.status),
        created_at=record.created_at,
        completed_at=record.completed_at,
    )


def _score_response(record: ScoreRecord) -> Score:
    return Score(
        id=record.id,
        run_id=record.run_id,
        case_id=record.case_id,
        experiment_item_id=record.experiment_item_id,
        repetition=record.repetition,
        attempt=record.attempt,
        evaluator_version_id=record.evaluator_version_id,
        trace_id=record.trace_id,
        span_id=record.span_id,
        source=ScoreSource(record.source),
        metric_name=record.metric_name,
        status=ScoreStatus(record.status),
        value=record.value,
        label=record.label,
        passed=record.passed,
        explanation=record.explanation,
        evidence=record.evidence,
        rubric=record.rubric,
        judge_model=record.judge_model,
        provenance=(
            ExternalScoreProvenance.model_validate(record.provenance)
            if record.provenance is not None
            else None
        ),
        threshold=record.threshold,
        direction=ScoreDirection(record.direction),
        raw_response=record.raw_response,
        raw_result=record.raw_result,
    )


def _audit_response(record: HumanScoreAuditRecord) -> HumanScoreAudit:
    return HumanScoreAudit.model_validate(
        {
            "id": record.id,
            "score_id": record.score_id,
            "action": record.action,
            "reviewer": record.reviewer,
            "previous_value": record.previous_value,
            "new_value": record.new_value,
            "created_at": record.created_at,
        }
    )


def _get_queue(db: Session, project_id: str, queue_id: str) -> AnnotationQueueRecord:
    queue = db.scalar(
        select(AnnotationQueueRecord).where(
            AnnotationQueueRecord.id == queue_id,
            AnnotationQueueRecord.project_id == project_id,
        )
    )
    if queue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="queue not found")
    return queue


def _get_item(
    db: Session, project_id: str, queue_id: str, item_id: str
) -> tuple[AnnotationQueueRecord, AnnotationQueueItemRecord]:
    row = db.execute(
        select(AnnotationQueueRecord, AnnotationQueueItemRecord)
        .join(
            AnnotationQueueItemRecord,
            AnnotationQueueItemRecord.queue_id == AnnotationQueueRecord.id,
        )
        .where(
            AnnotationQueueRecord.project_id == project_id,
            AnnotationQueueRecord.id == queue_id,
            AnnotationQueueItemRecord.id == item_id,
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="queue item not found")
    return row[0], row[1]


@router.post("", response_model=AnnotationQueue, status_code=status.HTTP_201_CREATED)
def create_annotation_queue(
    project_id: str,
    payload: AnnotationQueueCreateRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> AnnotationQueue:
    if db.get(ProjectRecord, project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    evaluator = db.scalar(
        select(EvaluatorVersionRecord).where(
            EvaluatorVersionRecord.id == payload.evaluator_version_id,
            EvaluatorVersionRecord.project_id == project_id,
        )
    )
    if evaluator is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="evaluator not found")
    if evaluator.evaluator_type != EvaluatorType.HUMAN.value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="annotation queue requires a human evaluator",
        )
    record = AnnotationQueueRecord(
        id=new_id(),
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        evaluator_version_id=evaluator.id,
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="annotation queue name already exists",
        ) from None
    db.refresh(record)
    return _queue_response(record)


@router.get("", response_model=list[AnnotationQueue])
def list_annotation_queues(
    project_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> list[AnnotationQueue]:
    records = db.scalars(
        select(AnnotationQueueRecord)
        .where(AnnotationQueueRecord.project_id == project_id)
        .order_by(AnnotationQueueRecord.created_at, AnnotationQueueRecord.id)
    ).all()
    return [_queue_response(record) for record in records]


@router.post(
    "/{queue_id}/items",
    response_model=AnnotationQueueItem,
    status_code=status.HTTP_201_CREATED,
)
def add_annotation_item(
    project_id: str,
    queue_id: str,
    payload: AnnotationQueueItemCreateRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> AnnotationQueueItem:
    queue = _get_queue(db, project_id, queue_id)
    row = db.execute(
        select(EvaluationRunRecord, CaseExecutionRecord, DatasetCaseRecord)
        .join(CaseExecutionRecord, CaseExecutionRecord.run_id == EvaluationRunRecord.id)
        .join(DatasetCaseRecord, DatasetCaseRecord.id == CaseExecutionRecord.case_id)
        .where(
            EvaluationRunRecord.project_id == project_id,
            EvaluationRunRecord.id == payload.run_id,
            DatasetCaseRecord.case_key == payload.case_id,
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run case not found",
        )
    run, execution, case = row
    evaluator_ids = [item["id"] for item in run.configuration_snapshot["evaluators"]]
    if queue.evaluator_version_id not in evaluator_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="queue evaluator was not selected for this run",
        )
    record = AnnotationQueueItemRecord(
        id=new_id(),
        queue_id=queue.id,
        run_id=run.id,
        case_id=case.case_key,
        trace_id=execution.trace_id,
        status=AnnotationStatus.PENDING.value,
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="run case is already in this annotation queue",
        ) from None
    db.refresh(record)
    return _item_response(record)


@router.get("/{queue_id}/items", response_model=list[AnnotationQueueItem])
def list_annotation_items(
    project_id: str,
    queue_id: str,
    item_status: AnnotationStatus | None = Query(default=None, alias="status"),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> list[AnnotationQueueItem]:
    queue = _get_queue(db, project_id, queue_id)
    statement = (
        select(AnnotationQueueItemRecord)
        .where(AnnotationQueueItemRecord.queue_id == queue.id)
        .order_by(AnnotationQueueItemRecord.created_at, AnnotationQueueItemRecord.id)
    )
    if item_status is not None:
        statement = statement.where(AnnotationQueueItemRecord.status == item_status.value)
    return [_item_response(record) for record in db.scalars(statement)]


def _sanitize_evidence(
    evidence: list[dict[str, Any]], settings: Settings
) -> list[dict[str, Any]]:
    return [sanitize_value(item, settings, PrivacyStats()) for item in evidence]


def _sanitize_text(value: str | None, settings: Settings) -> str | None:
    if value is None:
        return None
    sanitized = sanitize_value(value, settings, PrivacyStats())
    return sanitized if isinstance(sanitized, str) else json.dumps(sanitized, ensure_ascii=False)


def _snapshot(record: ScoreRecord) -> dict[str, Any]:
    return {
        "status": record.status,
        "value": record.value,
        "label": record.label,
        "passed": record.passed,
        "explanation": record.explanation,
        "evidence": record.evidence,
        "source": record.source,
    }


@router.put("/{queue_id}/items/{item_id}/score", response_model=Score)
def submit_human_score(
    project_id: str,
    queue_id: str,
    item_id: str,
    payload: HumanScoreRequest,
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    auth: AuthContext = Depends(require_project_access),  # noqa: B008
) -> Score:
    if auth.principal_type != "browser":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="human scores require an interactive browser session",
        )
    queue, item = _get_item(db, project_id, queue_id, item_id)
    evaluator = db.get(EvaluatorVersionRecord, queue.evaluator_version_id)
    if evaluator is None:  # pragma: no cover - protected by foreign key
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="evaluator not found")
    if payload.value is not None:
        if evaluator.score_min is not None and payload.value < evaluator.score_min:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="value is below evaluator score_min",
            )
        if evaluator.score_max is not None and payload.value > evaluator.score_max:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="value is above evaluator score_max",
            )

    records = db.scalars(
        select(ScoreRecord)
        .where(
            ScoreRecord.run_id == item.run_id,
            ScoreRecord.case_id == item.case_id,
            ScoreRecord.evaluator_version_id == evaluator.id,
            ScoreRecord.metric_name == evaluator.name,
        )
        .with_for_update()
    ).all()
    record = next((row for row in records if row.source == ScoreSource.HUMAN.value), None)
    placeholder = next(
        (
            row
            for row in records
            if row.source == ScoreSource.AUTOMATED.value
            and row.status == ScoreStatus.NOT_RUN.value
        ),
        None,
    )
    if record is None and placeholder is not None:
        record = placeholder
        record.source = ScoreSource.HUMAN.value
    if record is None or record.status == ScoreStatus.NOT_RUN.value:
        previous = None
        action = "created"
    else:
        previous = _snapshot(record)
        action = "updated"
    if record is None:
        record = ScoreRecord(
            id=new_id(),
            run_id=item.run_id,
            case_id=item.case_id,
            evaluator_version_id=evaluator.id,
            metric_name=evaluator.name,
            direction=evaluator.direction,
            source=ScoreSource.HUMAN.value,
        )
        db.add(record)
    record.trace_id = item.trace_id
    record.span_id = None
    record.status = ScoreStatus.PASSED.value if payload.passed else ScoreStatus.FAILED.value
    record.value = payload.value
    record.label = _sanitize_text(payload.label, settings)
    record.passed = payload.passed
    record.explanation = _sanitize_text(payload.explanation, settings)
    record.evidence = _sanitize_evidence(payload.evidence, settings)
    record.rubric = evaluator.rubric
    record.judge_model = None
    record.threshold = evaluator.default_threshold
    record.direction = evaluator.direction
    record.raw_result = {"source": "human_review"}
    db.flush()

    current = _snapshot(record)
    audit = HumanScoreAuditRecord(
        id=new_id(),
        score_id=record.id,
        project_id=project_id,
        action=action,
        reviewer=f"{auth.principal_type}:{auth.credential_id or 'workspace'}",
        previous_value=previous,
        new_value=current,
    )
    db.add(audit)
    item.status = AnnotationStatus.COMPLETED.value
    item.completed_at = datetime.now(UTC)
    db.commit()
    db.refresh(record)
    return _score_response(record)


@router.get("/{queue_id}/items/{item_id}/score", response_model=Score)
def get_human_score(
    project_id: str,
    queue_id: str,
    item_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> Score:
    """Return the persisted human score for one queue item, if it has been reviewed."""

    queue, item = _get_item(db, project_id, queue_id, item_id)
    record = db.scalar(
        select(ScoreRecord).where(
            ScoreRecord.run_id == item.run_id,
            ScoreRecord.case_id == item.case_id,
            ScoreRecord.evaluator_version_id == queue.evaluator_version_id,
            ScoreRecord.source == ScoreSource.HUMAN.value,
        )
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="human score not found")
    return _score_response(record)


@router.get("/{queue_id}/scores/{score_id}/audit", response_model=list[HumanScoreAudit])
def list_human_score_audit(
    project_id: str,
    queue_id: str,
    score_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> list[HumanScoreAudit]:
    queue = _get_queue(db, project_id, queue_id)
    score = db.scalar(
        select(ScoreRecord).where(
            ScoreRecord.id == score_id,
            ScoreRecord.evaluator_version_id == queue.evaluator_version_id,
        )
    )
    if score is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="human score not found")
    records = db.scalars(
        select(HumanScoreAuditRecord)
        .where(
            HumanScoreAuditRecord.score_id == score.id,
            HumanScoreAuditRecord.project_id == project_id,
        )
        .order_by(HumanScoreAuditRecord.created_at, HumanScoreAuditRecord.id)
    ).all()
    return [_audit_response(record) for record in records]
