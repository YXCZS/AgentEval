"""Project-scoped comparisons for runs over one immutable dataset version."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from agent_eval_api.attribution import attribute_first_error
from agent_eval_api.auth import AuthContext, get_db, require_project_access
from agent_eval_api.comparison_export import (
    export_comparison_json,
    export_comparison_markdown,
)
from agent_eval_api.contracts import (
    CaseComparison,
    CaseComparisonChange,
    CaseComparisonRun,
    ComparisonArtifactRequest,
    ComparisonEvidenceGap,
    ComparisonMetricPoint,
    ComparisonRequest,
    ComparisonRun,
    CriticalTaskImpact,
    EvaluationComparison,
    ExecutionStatus,
    ExternalScoreProvenance,
    GroupComparison,
    MetricComparison,
    RunStatus,
    Score,
    ScoreDirection,
    ScoreSource,
    ScoreStatus,
)
from agent_eval_api.db import (
    CaseExecutionRecord,
    DatasetCaseRecord,
    EvaluationRunRecord,
    ScoreRecord,
    TraceRecord,
)
from agent_eval_api.regression_gates import evaluate_regression_gate

router = APIRouter(prefix="/projects/{project_id}/comparisons", tags=["comparisons"])


@dataclass(frozen=True)
class _MetricStats:
    average: float | None
    pass_rate: float | None
    valid_count: int
    missing_count: int
    error_count: int
    passed_count: int
    missing_case_ids: tuple[str, ...]
    error_case_ids: tuple[str, ...]


@router.post("/artifact")
def export_comparison_artifact(
    project_id: str,
    payload: ComparisonArtifactRequest,
    export_format: Literal["json", "markdown"] = Query(default="json", alias="format"),
    db: Session = Depends(get_db),  # noqa: B008
    auth: AuthContext = Depends(require_project_access),  # noqa: B008
) -> Response:
    """Export one comparison in a stable format for CI or local review."""

    comparison = compare_runs(
        project_id,
        ComparisonRequest(run_ids=payload.run_ids),
        db,
        auth,
    )
    gate = None
    if payload.gate is not None:
        gate = evaluate_regression_gate(
            project_id,
            payload.run_ids[-1],
            payload.gate,
            db,
            auth,
        )
    if export_format == "markdown":
        content = export_comparison_markdown(comparison, gate)
        media_type = "text/markdown"
    else:
        content = export_comparison_json(comparison, gate)
        media_type = "application/json"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="agent-eval-comparison.{export_format}"'
            )
        },
    )


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


def _metric_stats(
    scores: list[ScoreRecord], case_ids: set[str]
) -> _MetricStats:
    scores_by_case: dict[str, list[ScoreRecord]] = defaultdict(list)
    for score in scores:
        if score.case_id is not None:
            scores_by_case[score.case_id].append(score)
    valid = [
        score
        for score in scores
        if score.status in {ScoreStatus.PASSED.value, ScoreStatus.FAILED.value}
    ]
    numeric = [score.value for score in valid if score.value is not None]
    passed_count = sum(score.passed is True for score in valid)
    missing_case_ids = {
        case_id
        for case_id in case_ids
        if not scores_by_case.get(case_id)
        or all(
            score.status in {ScoreStatus.MISSING.value, ScoreStatus.NOT_RUN.value}
            for score in scores_by_case[case_id]
        )
    }
    error_case_ids = {
        case_id
        for case_id, case_scores in scores_by_case.items()
        if any(score.status == ScoreStatus.ERROR.value for score in case_scores)
    }
    return _MetricStats(
        average=sum(numeric) / len(numeric) if numeric else None,
        pass_rate=passed_count / len(valid) if valid else None,
        valid_count=len(valid),
        missing_count=len(missing_case_ids),
        error_count=len(error_case_ids),
        passed_count=passed_count,
        missing_case_ids=tuple(sorted(missing_case_ids)),
        error_case_ids=tuple(sorted(error_case_ids)),
    )


def _point(
    run_id: str,
    stats: _MetricStats,
    baseline: _MetricStats | None,
) -> ComparisonMetricPoint:
    return ComparisonMetricPoint(
        run_id=run_id,
        average=stats.average,
        pass_rate=stats.pass_rate,
        valid_count=stats.valid_count,
        missing_count=stats.missing_count,
        error_count=stats.error_count,
        passed_count=stats.passed_count,
        missing_case_ids=list(stats.missing_case_ids),
        error_case_ids=list(stats.error_case_ids),
        delta_average=(
            stats.average - baseline.average
            if baseline is not None and stats.average is not None and baseline.average is not None
            else None
        ),
        delta_pass_rate=(
            stats.pass_rate - baseline.pass_rate
            if (
                baseline is not None
                and stats.pass_rate is not None
                and baseline.pass_rate is not None
            )
            else None
        ),
    )


def _evaluator_ids_by_metric(
    metric_name: str,
    run_ids: list[str],
    scores_by_run: dict[str, list[ScoreRecord]],
    evaluator_ids_by_run: dict[str, dict[str, set[str]]],
) -> tuple[list[str], dict[str, set[str]]]:
    identities: dict[str, set[str]] = {
        run_id: set(evaluator_ids_by_run.get(run_id, {}).get(metric_name, set()))
        for run_id in run_ids
    }
    for run_id in run_ids:
        identities[run_id].update(
            score.evaluator_version_id
            for score in scores_by_run[run_id]
            if score.metric_name == metric_name
        )
    return sorted({evaluator_id for ids in identities.values() for evaluator_id in ids}), identities


def _comparison_metadata(
    evaluator_ids_by_run: dict[str, set[str]],
) -> tuple[bool, str | None]:
    identities = list(evaluator_ids_by_run.values())
    if any(len(item) != 1 for item in identities):
        return False, "metric has missing or ambiguous evaluator identity across runs"
    if len({next(iter(item)) for item in identities}) > 1:
        return False, "metric uses different evaluator versions across runs"
    return True, None


def _metric_comparison(
    metric_name: str,
    run_ids: list[str],
    scores_by_run: dict[str, list[ScoreRecord]],
    case_ids: set[str],
    evaluator_ids_by_run: dict[str, dict[str, set[str]]],
    selected_case_ids: set[str] | None = None,
) -> tuple[bool, str | None, list[str], list[ComparisonMetricPoint]]:
    evaluator_ids, identities = _evaluator_ids_by_metric(
        metric_name, run_ids, scores_by_run, evaluator_ids_by_run
    )
    comparable, reason = _comparison_metadata(identities)
    selected = case_ids if selected_case_ids is None else selected_case_ids
    stats_by_run: dict[str, _MetricStats] = {}
    for run_id in run_ids:
        stats_by_run[run_id] = _metric_stats(
            [
                score
                for score in scores_by_run[run_id]
                if score.metric_name == metric_name and score.case_id in selected
            ],
            selected,
        )
    baseline = stats_by_run[run_ids[0]]
    return (
        comparable,
        reason,
        evaluator_ids,
        [
            _point(
                run_id,
                stats_by_run[run_id],
                baseline if comparable and index else None,
            )
            for index, run_id in enumerate(run_ids)
        ],
    )


def _metadata_group_values(
    metadata: dict[str, Any], group_by: Literal["category", "difficulty", "tag"]
) -> list[str]:
    if group_by in {"category", "difficulty"}:
        value = metadata.get(group_by)
        return [str(value)] if value is not None and str(value) else []
    raw = metadata.get("tags", [])
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, list):
        return [str(value) for value in raw if str(value)]
    return []


def _is_critical_case(metadata: dict[str, Any]) -> bool:
    """Accept the common dataset metadata spellings without guessing arbitrary values."""

    for key in ("critical", "is_critical", "critical_task"):
        value = metadata.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}:
            return True
    priority = metadata.get("priority")
    if isinstance(priority, str) and priority.strip().lower() == "critical":
        return True
    return "critical" in {
        tag.strip().lower()
        for tag in _metadata_group_values(metadata, "tag")
        if tag.strip()
    }


def _failed_metrics(scores: list[ScoreRecord]) -> list[str]:
    failed = [
        score.metric_name
        for score in scores
        if score.status != ScoreStatus.PASSED.value or score.passed is not True
    ]
    return sorted(set(failed))


def _case_failed(execution: CaseExecutionRecord | None, scores: list[ScoreRecord]) -> bool:
    if execution is None or execution.status != ExecutionStatus.COMPLETED.value:
        return True
    return not scores or bool(_failed_metrics(scores))


def _case_run(
    run_id: str,
    execution: CaseExecutionRecord | None,
    scores: list[ScoreRecord],
) -> CaseComparisonRun:
    return CaseComparisonRun(
        run_id=run_id,
        execution_status=(
            ExecutionStatus(execution.status) if execution is not None else ExecutionStatus.QUEUED
        ),
        output=execution.output if execution is not None else None,
        trace_id=execution.trace_id if execution is not None else None,
        error_type=execution.error_type if execution is not None else "missing_execution",
        error_message=(
            execution.error_message if execution is not None else "case execution not found"
        ),
        failed=_case_failed(execution, scores),
        scores=[_score(score) for score in scores],
    )


def _run_response(run: EvaluationRunRecord) -> ComparisonRun:
    return ComparisonRun(
        run_id=run.id,
        agent_version_id=run.agent_version_id,
        agent_version=dict(run.configuration_snapshot.get("agent_version", {})),
        dataset_version_id=run.dataset_version_id,
        status=RunStatus(run.status),
        total_cases=run.total_cases,
        completed_cases=run.completed_cases,
        failed_cases=run.failed_cases,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


@router.post("", response_model=EvaluationComparison)
def compare_runs(
    project_id: str,
    payload: ComparisonRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> EvaluationComparison:
    runs = db.scalars(
        select(EvaluationRunRecord).where(
            EvaluationRunRecord.project_id == project_id,
            EvaluationRunRecord.id.in_(payload.run_ids),
        )
    ).all()
    by_id = {run.id: run for run in runs}
    missing = [run_id for run_id in payload.run_ids if run_id not in by_id]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "evaluation run not found", "run_ids": missing},
        )
    ordered_runs = [by_id[run_id] for run_id in payload.run_ids]
    dataset_version_ids = {run.dataset_version_id for run in ordered_runs}
    if len(dataset_version_ids) != 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="all runs must use the same dataset version",
        )
    dataset_version_id = ordered_runs[0].dataset_version_id
    cases = db.scalars(
        select(DatasetCaseRecord)
        .where(DatasetCaseRecord.dataset_version_id == dataset_version_id)
        .order_by(DatasetCaseRecord.case_key)
    ).all()
    case_record_ids = {case.id for case in cases}
    case_keys = {case.case_key for case in cases}
    case_key_by_record_id = {case.id: case.case_key for case in cases}

    executions = db.scalars(
        select(CaseExecutionRecord).where(
            CaseExecutionRecord.run_id.in_(payload.run_ids),
            CaseExecutionRecord.case_id.in_(case_record_ids),
        )
    ).all()
    executions_by_run_case = {(item.run_id, item.case_id): item for item in executions}
    trace_ids = {item.trace_id for item in executions if item.trace_id is not None}
    traces = (
        db.scalars(
            select(TraceRecord)
            .where(TraceRecord.project_id == project_id, TraceRecord.id.in_(trace_ids))
            .options(selectinload(TraceRecord.spans))
        ).all()
        if trace_ids
        else []
    )
    traces_by_id = {trace.id: trace for trace in traces}
    scores = db.scalars(
        select(ScoreRecord).where(
            ScoreRecord.run_id.in_(payload.run_ids),
            ScoreRecord.case_id.in_(case_keys),
        )
    ).all()
    scores_by_run: dict[str, list[ScoreRecord]] = defaultdict(list)
    scores_by_run_case: dict[tuple[str, str], list[ScoreRecord]] = defaultdict(list)
    for score in scores:
        if score.run_id is None or score.case_id is None:
            continue
        scores_by_run[score.run_id].append(score)
        scores_by_run_case[(score.run_id, score.case_id)].append(score)
    for run_id in payload.run_ids:
        scores_by_run.setdefault(run_id, [])

    evaluator_ids_by_run: dict[str, dict[str, set[str]]] = {}
    for run in ordered_runs:
        metric_ids: dict[str, set[str]] = defaultdict(set)
        for evaluator in run.configuration_snapshot.get("evaluators", []):
            metric_name = evaluator.get("name")
            evaluator_id = evaluator.get("id")
            if metric_name and evaluator_id:
                metric_ids[str(metric_name)].add(str(evaluator_id))
        evaluator_ids_by_run[run.id] = metric_ids

    metric_names = sorted(
        {
            *(
                str(evaluator.get("name"))
                for run in ordered_runs
                for evaluator in run.configuration_snapshot.get("evaluators", [])
                if evaluator.get("name")
            ),
            *(score.metric_name for score in scores),
        }
    )
    metric_comparisons: list[MetricComparison] = []
    missing_evidence: list[ComparisonEvidenceGap] = []
    for metric_name in metric_names:
        comparable, reason, evaluator_ids, points = _metric_comparison(
            metric_name,
            payload.run_ids,
            scores_by_run,
            case_keys,
            evaluator_ids_by_run,
        )
        for point in points:
            if point.missing_case_ids:
                missing_evidence.append(
                    ComparisonEvidenceGap(
                        run_id=point.run_id,
                        metric_name=metric_name,
                        status="missing",
                        case_ids=point.missing_case_ids,
                    )
                )
            if point.error_case_ids:
                missing_evidence.append(
                    ComparisonEvidenceGap(
                        run_id=point.run_id,
                        metric_name=metric_name,
                        status="error",
                        case_ids=point.error_case_ids,
                    )
                )
        metric_comparisons.append(
            MetricComparison(
                metric_name=metric_name,
                evaluator_version_ids=evaluator_ids,
                comparable=comparable,
                reason=reason,
                points=points,
            )
        )

    group_comparisons: list[GroupComparison] = []
    group_by_fields: tuple[Literal["category", "difficulty", "tag"], ...] = (
        "category",
        "difficulty",
        "tag",
    )
    for group_by in group_by_fields:
        groups: dict[str, set[str]] = defaultdict(set)
        for case in cases:
            for group_value in _metadata_group_values(case.metadata_json, group_by):
                groups[group_value].add(case.id)
        for group_value in sorted(groups):
            for metric_name in metric_names:
                comparable, reason, evaluator_ids, points = _metric_comparison(
                    metric_name,
                    payload.run_ids,
                    scores_by_run,
                    case_keys,
                    evaluator_ids_by_run,
                    {case_key_by_record_id[case_id] for case_id in groups[group_value]},
                )
                group_comparisons.append(
                    GroupComparison(
                        group_by=group_by,
                        group_value=group_value,
                        metric_name=metric_name,
                        evaluator_version_ids=evaluator_ids,
                        comparable=comparable,
                        reason=reason,
                        points=points,
                    )
                )

    case_comparisons: list[CaseComparison] = []
    new_failures: list[CaseComparisonChange] = []
    recovered_cases: list[CaseComparisonChange] = []
    critical_by_run: dict[str, set[str]] = defaultdict(set)
    critical_case_ids = {
        case.case_key for case in cases if _is_critical_case(case.metadata_json)
    }
    baseline_run_id = payload.run_ids[0]
    for case in cases:
        case_runs: list[CaseComparisonRun] = []
        failed_by_run: dict[str, bool] = {}
        failed_metrics_by_run: dict[str, list[str]] = {}
        traces_by_run: dict[str, TraceRecord | None] = {}
        for run_id in payload.run_ids:
            case_scores = scores_by_run_case[(run_id, case.case_key)]
            execution = executions_by_run_case.get((run_id, case.id))
            traces_by_run[run_id] = (
                traces_by_id.get(execution.trace_id)
                if execution is not None and execution.trace_id is not None
                else None
            )
            failed_metrics = _failed_metrics(case_scores)
            represented_metrics = {score.metric_name for score in case_scores}
            failed_metrics.extend(
                metric_name
                for metric_name in metric_names
                if metric_name not in represented_metrics
            )
            failed_metrics = sorted(set(failed_metrics))
            if execution is None or execution.status != ExecutionStatus.COMPLETED.value:
                failed_metrics = sorted(set(failed_metrics + ["execution"]))
            failed_by_run[run_id] = _case_failed(execution, case_scores)
            failed_metrics_by_run[run_id] = failed_metrics
            if case.case_key in critical_case_ids and failed_by_run[run_id]:
                critical_by_run[run_id].add(case.case_key)
            case_runs.append(_case_run(run_id, execution, case_scores))
        first_error = None
        for run_id in payload.run_ids[1:]:
            if not failed_by_run[baseline_run_id] and failed_by_run[run_id]:
                first_error = attribute_first_error(
                    traces_by_run[baseline_run_id],
                    traces_by_run[run_id],
                    failed_metrics=failed_metrics_by_run[run_id],
                )
                break
        case_comparisons.append(
            CaseComparison(
                case_id=case.case_key,
                metadata=case.metadata_json,
                critical=case.case_key in critical_case_ids,
                runs=case_runs,
                first_error=first_error,
            )
        )
        for run_id in payload.run_ids[1:]:
            if not failed_by_run[baseline_run_id] and failed_by_run[run_id]:
                new_failures.append(
                    CaseComparisonChange(
                        case_id=case.case_key,
                        run_id=run_id,
                        baseline_run_id=baseline_run_id,
                        failed_metrics=failed_metrics_by_run[run_id],
                        critical=case.case_key in critical_case_ids,
                        first_error=attribute_first_error(
                            traces_by_run[baseline_run_id],
                            traces_by_run[run_id],
                            failed_metrics=failed_metrics_by_run[run_id],
                        ),
                    )
                )
            elif failed_by_run[baseline_run_id] and not failed_by_run[run_id]:
                recovered_cases.append(
                    CaseComparisonChange(
                        case_id=case.case_key,
                        run_id=run_id,
                        baseline_run_id=baseline_run_id,
                        failed_metrics=failed_metrics_by_run[baseline_run_id],
                        critical=case.case_key in critical_case_ids,
                    )
                )

    critical_task_impact = [
        CriticalTaskImpact(
            candidate_run_id=run_id,
            critical_case_count=len(critical_case_ids),
            baseline_failed_count=len(critical_by_run[baseline_run_id]),
            candidate_failed_count=len(critical_by_run[run_id]),
            newly_regressed_case_ids=sorted(
                critical_by_run[run_id] - critical_by_run[baseline_run_id]
            ),
            recovered_case_ids=sorted(
                critical_by_run[baseline_run_id] - critical_by_run[run_id]
            ),
        )
        for run_id in payload.run_ids[1:]
    ]

    return EvaluationComparison(
        dataset_version_id=dataset_version_id,
        baseline_run_id=baseline_run_id,
        runs=[_run_response(run) for run in ordered_runs],
        metric_comparisons=metric_comparisons,
        group_comparisons=group_comparisons,
        case_comparisons=case_comparisons,
        new_failures=new_failures,
        recovered_cases=recovered_cases,
        missing_evidence=missing_evidence,
        critical_task_impact=critical_task_impact,
        generated_at=datetime.now(UTC),
    )
