"""Portable JSON and CSV exports for filtered evaluation reports."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from agent_eval_api.contracts import EvaluationReport, Score


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def report_document(
    report: EvaluationReport,
    configuration_snapshot: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": report.generated_at.isoformat(),
        "run": {
            "id": report.run_id,
            "status": report.status.value,
            "configuration_snapshot": configuration_snapshot,
        },
        "filters": report.filters,
        "metrics": [metric.model_dump(mode="json") for metric in report.metrics],
        "cases": [case.model_dump(mode="json") for case in report.cases],
    }


def export_report_json(
    report: EvaluationReport,
    configuration_snapshot: dict[str, Any],
) -> str:
    return json.dumps(
        report_document(report, configuration_snapshot),
        ensure_ascii=False,
        indent=2,
    )


def export_report_csv(
    report: EvaluationReport,
    configuration_snapshot: dict[str, Any],
) -> str:
    evaluators = {
        item["id"]: item for item in configuration_snapshot.get("evaluators", [])
    }
    metrics = {
        (item.metric_name, item.evaluator_version_id): item for item in report.metrics
    }
    fieldnames = [
        "run_id",
        "run_status",
        "agent_version_id",
        "agent_version",
        "dataset_version_id",
        "dataset_version",
        "case_id",
        "execution_status",
        "error_type",
        "error_message",
        "metadata",
        "output",
        "trace_id",
        "metric_name",
        "evaluator_version_id",
        "evaluator_version",
        "score_status",
        "value",
        "label",
        "passed",
        "explanation",
        "evidence",
        "rubric",
        "judge_model",
        "provenance",
        "raw_response",
        "threshold",
        "direction",
        "aggregate_valid_count",
        "aggregate_missing_count",
        "aggregate_error_count",
        "aggregate_average",
        "aggregate_pass_rate",
        "aggregation",
        "generated_at",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    agent = configuration_snapshot.get("agent_version", {})
    dataset = configuration_snapshot.get("dataset_version", {})
    for case in report.cases:
        scores: list[Score | None] = list(case.scores) or [None]
        for score in scores:
            evaluator = evaluators.get(score.evaluator_version_id, {}) if score else {}
            aggregate = (
                metrics.get((score.metric_name, score.evaluator_version_id)) if score else None
            )
            writer.writerow(
                {
                    "run_id": report.run_id,
                    "run_status": report.status.value,
                    "agent_version_id": agent.get("version_id", ""),
                    "agent_version": agent.get("version", ""),
                    "dataset_version_id": dataset.get("id", ""),
                    "dataset_version": dataset.get("version", ""),
                    "case_id": case.case_id,
                    "execution_status": case.execution_status.value,
                    "error_type": case.error_type or "",
                    "error_message": case.error_message or "",
                    "metadata": _dump(case.metadata),
                    "output": _dump(case.output),
                    "trace_id": case.trace_id or "",
                    "metric_name": score.metric_name if score else "",
                    "evaluator_version_id": score.evaluator_version_id if score else "",
                    "evaluator_version": evaluator.get("version", ""),
                    "score_status": score.status.value if score else "",
                    "value": score.value if score and score.value is not None else "",
                    "label": score.label if score and score.label is not None else "",
                    "passed": score.passed if score and score.passed is not None else "",
                    "explanation": score.explanation if score else "",
                    "evidence": _dump(score.evidence) if score else "[]",
                    "rubric": score.rubric if score else evaluator.get("rubric", ""),
                    "judge_model": (
                        score.judge_model if score else evaluator.get("judge_model", "")
                    ),
                    "provenance": _dump(score.provenance.model_dump(mode="json"))
                    if score and score.provenance
                    else "{}",
                    "raw_response": _dump(score.raw_response) if score else "null",
                    "threshold": (
                        score.threshold if score else evaluator.get("default_threshold", "")
                    ),
                    "direction": score.direction.value if score else evaluator.get("direction", ""),
                    "aggregate_valid_count": aggregate.valid_count if aggregate else "",
                    "aggregate_missing_count": aggregate.missing_count if aggregate else "",
                    "aggregate_error_count": aggregate.error_count if aggregate else "",
                    "aggregate_average": (
                        aggregate.average
                        if aggregate is not None and aggregate.average is not None
                        else ""
                    ),
                    "aggregate_pass_rate": (
                        aggregate.pass_rate
                        if aggregate is not None and aggregate.pass_rate is not None
                        else ""
                    ),
                    "aggregation": aggregate.aggregation if aggregate else "",
                    "generated_at": report.generated_at.isoformat(),
                }
            )
    return buffer.getvalue()
