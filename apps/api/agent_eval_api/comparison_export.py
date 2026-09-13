"""Portable JSON and Markdown artifacts for baseline/candidate comparisons."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from agent_eval_api.contracts import (
    ComparisonArtifact,
    EvaluationComparison,
    RegressionGateResult,
)


def comparison_artifact_document(
    comparison: EvaluationComparison,
    gate: RegressionGateResult | None = None,
) -> ComparisonArtifact:
    """Build the stable JSON shape consumed by CI and other automation."""

    return ComparisonArtifact(
        generated_at=comparison.generated_at,
        baseline_run_id=comparison.baseline_run_id,
        candidate_run_id=comparison.runs[1].run_id,
        comparison=comparison,
        gate=gate,
    )


def export_comparison_json(
    comparison: EvaluationComparison,
    gate: RegressionGateResult | None = None,
) -> str:
    artifact = comparison_artifact_document(comparison, gate)
    return json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2)


def _cell(value: Any) -> str:
    text = "-" if value is None or value == "" else str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _slug(value: str, prefix: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    if not slug:
        slug = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{slug}"


def _link(label: str, target: str) -> str:
    return f"[{_cell(label)}](#{target})"


def _trace_link(trace_id: str | None) -> str:
    if not trace_id:
        return "-"
    return _link(trace_id, _slug(trace_id, "trace"))


def _run_link(run_id: str) -> str:
    return _link(run_id, _slug(run_id, "run"))


def _point_value(point: Any, field: str) -> str:
    value = getattr(point, field)
    return "-" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)


def _case_run(case: Any, run_id: str) -> Any | None:
    return next((item for item in case.runs if item.run_id == run_id), None)


def export_comparison_markdown(
    comparison: EvaluationComparison,
    gate: RegressionGateResult | None = None,
) -> str:
    """Render a human-readable artifact while retaining machine identifiers."""

    baseline_id = comparison.baseline_run_id
    candidate_id = comparison.runs[1].run_id
    lines = [
        "# Agent Evaluation Comparison",
        "",
        f"- Dataset version: `{_cell(comparison.dataset_version_id)}`",
        f"- Baseline: {_run_link(baseline_id)}",
        f"- Candidate: {_run_link(candidate_id)}",
        f"- Generated at: `{_cell(comparison.generated_at.isoformat())}`",
        "",
        "## Gate",
        "",
    ]
    if gate is None:
        lines.append("Gate was not evaluated for this artifact.")
    else:
        lines.append(f"**Status: `{gate.status.value}`**")
        if gate.policy_version:
            lines.append(f"Policy version: `{_cell(gate.policy_version)}`")
        lines.extend(
            [
                "",
                "| Rule | Status | Actual | Reason |",
                "| --- | --- | ---: | --- |",
            ]
        )
        for result in gate.rules:
            lines.append(
                "| "
                + " | ".join(
                    (
                        _cell(result.rule.metric_name),
                        _cell(result.status.value),
                        _cell(result.actual_value),
                        _cell(result.reason),
                    )
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## Metric Comparison",
            "",
            "| Metric | Baseline average | Candidate average | Delta average | "
            "Baseline pass rate | Candidate pass rate | Delta pass rate | Comparable |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for metric in comparison.metric_comparisons:
        baseline = next((item for item in metric.points if item.run_id == baseline_id), None)
        candidate = next((item for item in metric.points if item.run_id == candidate_id), None)
        lines.append(
            "| "
            + " | ".join(
                (
                    _cell(metric.metric_name),
                    _point_value(baseline, "average") if baseline else "-",
                    _point_value(candidate, "average") if candidate else "-",
                    _point_value(candidate, "delta_average") if candidate else "-",
                    _point_value(baseline, "pass_rate") if baseline else "-",
                    _point_value(candidate, "pass_rate") if candidate else "-",
                    _point_value(candidate, "delta_pass_rate") if candidate else "-",
                    _cell("yes" if metric.comparable else f"no: {metric.reason or 'unknown'}"),
                )
            )
            + " |"
        )

    lines.extend(["", "## Newly Regressed Cases", ""])
    if not comparison.new_failures:
        lines.append("No newly regressed cases.")
    else:
        lines.extend(
            [
                "| Case | Candidate run | Failed metrics | First-error diagnosis | "
                "Baseline trace | Candidate trace |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        cases_by_id = {case.case_id: case for case in comparison.case_comparisons}
        for failure in comparison.new_failures:
            case = cases_by_id.get(failure.case_id)
            diagnosis = failure.first_error or (case.first_error if case else None)
            baseline = _case_run(case, baseline_id) if case else None
            candidate = _case_run(case, candidate_id) if case else None
            diagnosis_text = (
                f"{diagnosis.category}: {diagnosis.reason}" if diagnosis else "-"
            )
            lines.append(
                "| "
                + " | ".join(
                    (
                        _link(failure.case_id, _slug(failure.case_id, "case")),
                        _run_link(failure.run_id),
                        _cell(", ".join(failure.failed_metrics)),
                        _cell(diagnosis_text),
                        _trace_link(baseline.trace_id if baseline else None),
                        _trace_link(candidate.trace_id if candidate else None),
                    )
                )
                + " |"
            )

    lines.extend(["", "## Failure Evidence", ""])
    if not comparison.new_failures:
        lines.append("No failure evidence to inspect.")
    else:
        cases_by_id = {case.case_id: case for case in comparison.case_comparisons}
        for failure in comparison.new_failures:
            case = cases_by_id.get(failure.case_id)
            diagnosis = failure.first_error or (case.first_error if case else None)
            baseline = _case_run(case, baseline_id) if case else None
            candidate = _case_run(case, candidate_id) if case else None
            lines.extend(
                [
                    f'<a id="{_slug(failure.case_id, "case")}"></a>',
                    f"### Case `{_cell(failure.case_id)}`",
                    f"- Baseline run: {_run_link(baseline_id)}",
                    f"- Candidate run: {_run_link(failure.run_id)}",
                    f"- Baseline trace: {_trace_link(baseline.trace_id if baseline else None)}",
                    f"- Candidate trace: {_trace_link(candidate.trace_id if candidate else None)}",
                ]
            )
            if diagnosis:
                lines.extend(
                    [
                        f"- First error: `{_cell(diagnosis.category)}`: {_cell(diagnosis.reason)}",
                        f"- Baseline span: `{_cell(diagnosis.baseline_span_id)}`",
                        f"- Candidate span: `{_cell(diagnosis.candidate_span_id)}`",
                    ]
                )
            lines.append("")

    lines.extend(["", "## Run Index", ""])
    for run in comparison.runs:
        lines.extend(
            [
                f'<a id="{_slug(run.run_id, "run")}"></a>',
                f"- `{_cell(run.run_id)}`: `{_cell(run.status.value)}`, "
                f"agent `{_cell(run.agent_version_id)}`",
            ]
        )
    lines.extend(["", "## Trace Index", ""])
    trace_ids = sorted(
        {
            run.trace_id
            for case in comparison.case_comparisons
            for run in case.runs
            if run.trace_id
        }
    )
    if trace_ids:
        for trace_id in trace_ids:
            lines.extend(
                [
                    f'<a id="{_slug(trace_id, "trace")}"></a>',
                    f"- `{_cell(trace_id)}`",
                ]
            )
    else:
        lines.append("No traces were recorded.")
    return "\n".join(lines) + "\n"
