"""Stable per-metric aggregation with explicit missing and error accounting."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from agent_eval_api.contracts import ScoreStatus
from agent_eval_api.db import (
    AggregateMetricRecord,
    EvaluationRunRecord,
    ScoreRecord,
    new_id,
)


def _aggregation(scores: list[ScoreRecord]) -> str:
    numeric = [score.value for score in scores if score.value is not None]
    if numeric and all(value in {0.0, 1.0} for value in numeric):
        return "pass_rate"
    return "mean"


def aggregate_run_scores(
    db: Session,
    run: EvaluationRunRecord,
) -> list[AggregateMetricRecord]:
    """Rebuild aggregates from Score rows; missing data is never treated as passing."""

    # Finalize requests and managed Judge workers can rebuild the same run at
    # the same time. Serialize the delete/reinsert window on the run row so
    # PostgreSQL cannot interleave two aggregate rebuilds and hit the unique
    # (run_id, metric_name, evaluator_version_id) constraint.
    db.scalar(
        select(EvaluationRunRecord)
        .where(EvaluationRunRecord.id == run.id)
        .with_for_update()
    )
    scores = db.scalars(
        select(ScoreRecord).where(ScoreRecord.run_id == run.id)
    ).all()
    grouped: dict[tuple[str, str], list[ScoreRecord]] = defaultdict(list)
    evaluator_metadata: dict[tuple[str, str], tuple[float | None, str]] = {}
    for snapshot in run.configuration_snapshot.get("evaluators", []):
        evaluator_id = str(snapshot["id"])
        metric_name = str(snapshot["name"])
        key = (metric_name, evaluator_id)
        evaluator_metadata[key] = (
            snapshot.get("default_threshold"),
            str(snapshot.get("direction") or "higher_is_better"),
        )
    for score in scores:
        grouped[(score.metric_name, score.evaluator_version_id)].append(score)
        evaluator_metadata.setdefault(
            (score.metric_name, score.evaluator_version_id),
            (score.threshold, score.direction),
        )

    db.execute(delete(AggregateMetricRecord).where(AggregateMetricRecord.run_id == run.id))
    # Execute the rebuild delete before adding rows with the same unique keys.
    # PostgreSQL otherwise may order the pending INSERT ahead of the DELETE
    # during flush, making repeated finalize calls fail with a unique violation.
    db.flush()
    records: list[AggregateMetricRecord] = []
    for metric_name, evaluator_version_id in sorted(
        set(grouped) | set(evaluator_metadata)
    ):
        metric_scores = grouped.get((metric_name, evaluator_version_id), [])
        valid = [
            score
            for score in metric_scores
            if score.status in {ScoreStatus.PASSED.value, ScoreStatus.FAILED.value}
        ]
        represented_items = {
            (score.case_id, score.repetition)
            for score in metric_scores
            if score.case_id is not None
        }
        missing_count = max(0, run.total_cases - len(represented_items)) + sum(
            score.status in {ScoreStatus.MISSING.value, ScoreStatus.NOT_RUN.value}
            for score in metric_scores
        )
        error_count = sum(score.status == ScoreStatus.ERROR.value for score in metric_scores)
        passed_count = sum(score.passed is True for score in valid)
        numeric = [score.value for score in valid if score.value is not None]
        threshold, direction = evaluator_metadata[(metric_name, evaluator_version_id)]
        record = AggregateMetricRecord(
            id=new_id(),
            run_id=run.id,
            metric_name=metric_name,
            evaluator_version_id=evaluator_version_id,
            valid_count=len(valid),
            missing_count=missing_count,
            error_count=error_count,
            passed_count=passed_count,
            average=sum(numeric) / len(numeric) if numeric else None,
            pass_rate=passed_count / len(valid) if valid else None,
            aggregation=_aggregation(valid),
            threshold=threshold,
            direction=direction,
        )
        db.add(record)
        records.append(record)
    db.flush()
    return records
