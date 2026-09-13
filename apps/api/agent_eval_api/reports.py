"""Project-scoped evaluation reports with sample filters and dynamic aggregates."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from agent_eval_api.auth import AuthContext, get_db, require_project_access
from agent_eval_api.contracts import (
    AggregateMetric,
    EvaluationReport,
    EvaluationReportCase,
    EvaluationReportSummary,
    ExecutionStatus,
    ExternalScoreProvenance,
    RunStatus,
    Score,
    ScoreDirection,
    ScoreSource,
    ScoreStatus,
)
from agent_eval_api.db import (
    AggregateMetricRecord,
    CaseExecutionRecord,
    EvaluationRunRecord,
    ScoreRecord,
)
from agent_eval_api.report_export import export_report_csv, export_report_json

router = APIRouter(prefix="/projects/{project_id}/reports", tags=["reports"])


def _score(record: ScoreRecord) -> Score:
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


def _aggregate_records(records: Sequence[AggregateMetricRecord]) -> list[AggregateMetric]:
    return [
        AggregateMetric(
            metric_name=record.metric_name,
            evaluator_version_id=record.evaluator_version_id,
            valid_count=record.valid_count,
            missing_count=record.missing_count,
            error_count=record.error_count,
            passed_count=record.passed_count,
            average=record.average,
            pass_rate=record.pass_rate,
            aggregation=record.aggregation,  # type: ignore[arg-type]
            threshold=record.threshold,
            direction=ScoreDirection(record.direction),
        )
        for record in records
    ]


def _dynamic_aggregates(scores: Sequence[ScoreRecord]) -> list[AggregateMetric]:
    grouped: dict[tuple[str, str], list[ScoreRecord]] = defaultdict(list)
    for score in scores:
        grouped[(score.metric_name, score.evaluator_version_id)].append(score)
    metrics: list[AggregateMetric] = []
    for (metric_name, evaluator_id), group in sorted(grouped.items()):
        valid = [
            score
            for score in group
            if score.status in {ScoreStatus.PASSED.value, ScoreStatus.FAILED.value}
        ]
        numeric = [score.value for score in valid if score.value is not None]
        passed_count = sum(score.passed is True for score in valid)
        binary = bool(numeric) and all(value in {0.0, 1.0} for value in numeric)
        metrics.append(
            AggregateMetric(
                metric_name=metric_name,
                evaluator_version_id=evaluator_id,
                valid_count=len(valid),
                missing_count=sum(
                    score.status in {ScoreStatus.MISSING.value, ScoreStatus.NOT_RUN.value}
                    for score in group
                ),
                error_count=sum(score.status == ScoreStatus.ERROR.value for score in group),
                passed_count=passed_count,
                average=sum(numeric) / len(numeric) if numeric else None,
                pass_rate=passed_count / len(valid) if valid else None,
                aggregation="pass_rate" if binary else "mean",
                threshold=group[0].threshold,
                direction=ScoreDirection(group[0].direction),
            )
        )
    return metrics


def _tags(metadata: dict[str, Any]) -> list[str]:
    raw = metadata.get("tags", [])
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        return [raw]
    return []


@router.get("", response_model=list[EvaluationReportSummary])
def list_reports(
    project_id: str,
    run_status: RunStatus | None = Query(default=None, alias="status"),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> list[EvaluationReportSummary]:
    statement = (
        select(EvaluationRunRecord)
        .where(EvaluationRunRecord.project_id == project_id)
        .order_by(EvaluationRunRecord.created_at.desc(), EvaluationRunRecord.id.desc())
    )
    if run_status is not None:
        statement = statement.where(EvaluationRunRecord.status == run_status.value)
    runs = db.scalars(statement).all()
    result: list[EvaluationReportSummary] = []
    for run in runs:
        aggregates = db.scalars(
            select(AggregateMetricRecord).where(AggregateMetricRecord.run_id == run.id)
        ).all()
        result.append(
            EvaluationReportSummary(
                run_id=run.id,
                status=RunStatus(run.status),
                agent_version_id=run.agent_version_id,
                dataset_version_id=run.dataset_version_id,
                total_cases=run.total_cases,
                completed_cases=run.completed_cases,
                failed_cases=run.failed_cases,
                metrics=_aggregate_records(aggregates),
                created_at=run.created_at,
                finished_at=run.finished_at,
            )
        )
    return result


@router.get("/{run_id}", response_model=EvaluationReport)
def read_report(
    project_id: str,
    run_id: str,
    metric: str | None = None,
    category: str | None = None,
    difficulty: str | None = None,
    tag: str | None = None,
    error_type: str | None = None,
    execution_status: ExecutionStatus | None = Query(default=None, alias="execution_status"),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> EvaluationReport:
    run = db.scalar(
        select(EvaluationRunRecord).where(
            EvaluationRunRecord.id == run_id,
            EvaluationRunRecord.project_id == project_id,
        )
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")
    executions = db.scalars(
        select(CaseExecutionRecord)
        .where(CaseExecutionRecord.run_id == run.id)
        .options(selectinload(CaseExecutionRecord.dataset_case))
    ).all()
    matched: list[CaseExecutionRecord] = []
    for execution in executions:
        metadata = execution.dataset_case.metadata_json
        if category is not None and metadata.get("category") != category:
            continue
        if difficulty is not None and metadata.get("difficulty") != difficulty:
            continue
        if tag is not None and tag not in _tags(metadata):
            continue
        if error_type is not None and execution.error_type != error_type:
            continue
        if execution_status is not None and execution.status != execution_status.value:
            continue
        matched.append(execution)
    case_ids = [execution.dataset_case.case_key for execution in matched]
    score_statement = select(ScoreRecord).where(
        ScoreRecord.run_id == run.id,
        ScoreRecord.case_id.in_(case_ids),
    )
    if metric is not None:
        score_statement = score_statement.where(ScoreRecord.metric_name == metric)
    scores = db.scalars(score_statement).all() if case_ids else []
    scores_by_case: dict[str, list[ScoreRecord]] = defaultdict(list)
    for score in scores:
        if score.case_id is not None:
            scores_by_case[score.case_id].append(score)
    filters = {
        key: value
        for key, value in {
            "metric": metric,
            "category": category,
            "difficulty": difficulty,
            "tag": tag,
            "error_type": error_type,
            "execution_status": execution_status.value if execution_status else None,
        }.items()
        if value is not None
    }
    return EvaluationReport(
        run_id=run.id,
        status=RunStatus(run.status),
        total_cases=run.total_cases,
        matched_cases=len(matched),
        filters=filters,
        metrics=_dynamic_aggregates(scores),
        cases=[
            EvaluationReportCase(
                case_id=execution.dataset_case.case_key,
                metadata=execution.dataset_case.metadata_json,
                execution_status=ExecutionStatus(execution.status),
                error_type=execution.error_type,
                error_message=execution.error_message,
                output=execution.output,
                trace_id=execution.trace_id,
                scores=[_score(score) for score in scores_by_case[execution.dataset_case.case_key]],
            )
            for execution in sorted(matched, key=lambda item: item.dataset_case.case_key)
        ],
        generated_at=datetime.now(UTC),
    )


@router.get("/{run_id}/export")
def export_report(
    project_id: str,
    run_id: str,
    export_format: Literal["json", "csv"] = Query(default="json", alias="format"),
    metric: str | None = None,
    category: str | None = None,
    difficulty: str | None = None,
    tag: str | None = None,
    error_type: str | None = None,
    execution_status: ExecutionStatus | None = Query(default=None),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    auth: AuthContext = Depends(require_project_access),  # noqa: B008
) -> Response:
    report = read_report(
        project_id,
        run_id,
        metric,
        category,
        difficulty,
        tag,
        error_type,
        execution_status,
        db,
        auth,
    )
    run = db.get(EvaluationRunRecord, run_id)
    assert run is not None
    if export_format == "csv":
        content = export_report_csv(report, run.configuration_snapshot)
        media_type = "text/csv"
    else:
        content = export_report_json(report, run.configuration_snapshot)
        media_type = "application/json"
    filename = f"evaluation-report-{run_id}.{export_format}"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
