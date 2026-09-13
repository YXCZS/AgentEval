"""Conservative first-error attribution for aligned Agent traces."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from agent_eval_api.contracts import FirstErrorAttribution
from agent_eval_api.db import TraceRecord, TraceSpanRecord

AttributionCategory = Literal[
    "tool_selection",
    "tool_arguments",
    "tool_execution",
    "retrieval",
    "final_answer",
    "format",
    "timeout",
    "cost_or_latency",
    "indeterminate",
]

_OBSERVABLE_KINDS = {"tool", "tool_result", "retrieval", "llm", "guardrail"}


@dataclass(frozen=True)
class _SpanView:
    span: TraceSpanRecord
    kind: str
    name: str
    arguments: Any


def _public_trace_id(trace: TraceRecord | None) -> str | None:
    return None if trace is None else trace.trace_id or trace.id


def _tool_name(span: TraceSpanRecord) -> str:
    for container in (span.attributes or {}, span.extensions or {}):
        for key in ("tool.name", "tool_name", "name"):
            value = container.get(key)
            if isinstance(value, str) and value:
                return value
    return span.name


def _tool_arguments(span: TraceSpanRecord) -> Any:
    for container in (span.attributes or {}, span.extensions or {}):
        for key in ("tool.call.arguments", "tool.parameters", "tool.arguments"):
            if key in container:
                return container[key]
    return span.input


def _ordered_observations(trace: TraceRecord | None) -> list[_SpanView]:
    if trace is None:
        return []

    def time_key(span: TraceSpanRecord) -> tuple[int, str]:
        raw_nanos = (span.extensions or {}).get("otel.start_time_unix_nano")
        if isinstance(raw_nanos, int):
            return raw_nanos, span.span_id
        return int(span.started_at.timestamp() * 1_000_000_000), span.span_id

    by_parent: dict[str | None, list[TraceSpanRecord]] = {}
    span_ids = {span.span_id for span in trace.spans}
    for span in trace.spans:
        parent_id = span.parent_span_id if span.parent_span_id in span_ids else None
        by_parent.setdefault(parent_id, []).append(span)
    for children in by_parent.values():
        children.sort(key=time_key)

    spans: list[TraceSpanRecord] = []

    def visit(span: TraceSpanRecord) -> None:
        spans.append(span)
        for child in by_parent.get(span.span_id, []):
            visit(child)

    for root in by_parent.get(None, []):
        visit(root)
    return [
        _SpanView(
            span=span,
            kind=span.kind,
            name=_tool_name(span) if span.kind in {"tool", "tool_result"} else span.name,
            arguments=_tool_arguments(span),
        )
        for span in spans
        if span.kind in _OBSERVABLE_KINDS
    ]


def _stable(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return repr(value)


def _text(value: Any) -> str:
    return value.lower() if isinstance(value, str) else _stable(value).lower()


def _is_timeout(span: TraceSpanRecord | None, trace: TraceRecord | None = None) -> bool:
    values: list[Any] = []
    if span is not None:
        values.extend([span.error, span.attributes or {}, span.extensions or {}])
    if trace is not None:
        values.append(trace.extensions or {})
    return any(
        token in _text(value)
        for value in values
        for token in ("timeout", "timed out", "deadline exceeded")
    )


def _duration_ms(span: TraceSpanRecord) -> float | None:
    if span.ended_at is None:
        return None
    return max(0.0, (span.ended_at - span.started_at).total_seconds() * 1000)


def _trace_latency(trace: TraceRecord | None) -> float | None:
    if trace is None:
        return None
    value = (trace.extensions or {}).get("agent_eval.latency_ms")
    if isinstance(value, (int, float)):
        return float(value)
    durations = [_duration_ms(span) for span in trace.spans]
    numeric = [item for item in durations if item is not None]
    return max(numeric) if numeric else None


def _trace_cost(trace: TraceRecord | None) -> float | None:
    if trace is None:
        return None
    values: list[Any] = [(trace.extensions or {}).get("cost")]
    values.extend(span.cost for span in trace.spans)
    for value in values:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, dict):
            for key in ("total", "amount", "value"):
                if isinstance(value.get(key), (int, float)):
                    return float(value[key])
    return None


def _failed_metric_category(failed_metrics: Iterable[str]) -> AttributionCategory | None:
    names = {metric.lower() for metric in failed_metrics}
    if any("format" in metric or "schema" in metric for metric in names):
        return "format"
    if any("latency" in metric or "cost" in metric for metric in names):
        return "cost_or_latency"
    return None


def _diagnosis(
    category: AttributionCategory,
    reason: str,
    baseline: TraceRecord | None,
    candidate: TraceRecord | None,
    *,
    baseline_span: TraceSpanRecord | None = None,
    candidate_span: TraceSpanRecord | None = None,
    evidence: list[dict[str, Any]] | None = None,
) -> FirstErrorAttribution:
    return FirstErrorAttribution(
        category=category,
        reason=reason,
        baseline_trace_id=_public_trace_id(baseline),
        candidate_trace_id=_public_trace_id(candidate),
        baseline_span_id=baseline_span.span_id if baseline_span is not None else None,
        candidate_span_id=candidate_span.span_id if candidate_span is not None else None,
        evidence=evidence or [],
    )


def attribute_first_error(
    baseline: TraceRecord | None,
    candidate: TraceRecord | None,
    *,
    failed_metrics: Iterable[str] = (),
) -> FirstErrorAttribution:
    """Return the earliest defensible divergence, never a guessed root cause."""

    if baseline is None or candidate is None:
        return _diagnosis(
            "indeterminate",
            "baseline and candidate traces are both required for attribution",
            baseline,
            candidate,
        )

    baseline_spans = _ordered_observations(baseline)
    candidate_spans = _ordered_observations(candidate)
    if not baseline_spans or not candidate_spans:
        return _diagnosis(
            "indeterminate",
            "one or both traces have no alignable observable spans",
            baseline,
            candidate,
        )

    for index in range(max(len(baseline_spans), len(candidate_spans))):
        baseline_view = baseline_spans[index] if index < len(baseline_spans) else None
        candidate_view = candidate_spans[index] if index < len(candidate_spans) else None
        if baseline_view is None or candidate_view is None:
            present = candidate_view or baseline_view
            if present is not None and present.kind in {"tool", "tool_result"}:
                return _diagnosis(
                    "tool_selection",
                    "tool trajectory length diverged at the first unmatched tool span",
                    baseline,
                    candidate,
                    baseline_span=baseline_view.span if baseline_view else None,
                    candidate_span=candidate_view.span if candidate_view else None,
                    evidence=[{"field": "trajectory_length", "index": index}],
                )
            return _diagnosis(
                "indeterminate",
                "observable spans could not be aligned after the first shared span",
                baseline,
                candidate,
                baseline_span=baseline_view.span if baseline_view else None,
                candidate_span=candidate_view.span if candidate_view else None,
                evidence=[{"field": "trajectory_length", "index": index}],
            )

        baseline_span = baseline_view.span
        candidate_span = candidate_view.span
        if baseline_view.kind != candidate_view.kind:
            if baseline_view.kind in {"tool", "tool_result"} or candidate_view.kind in {
                "tool",
                "tool_result",
            }:
                return _diagnosis(
                    "tool_selection",
                    "the first aligned tool operation has a different span kind",
                    baseline,
                    candidate,
                    baseline_span=baseline_span,
                    candidate_span=candidate_span,
                    evidence=[
                        {
                            "field": "kind",
                            "baseline": baseline_view.kind,
                            "candidate": candidate_view.kind,
                            "index": index,
                        }
                    ],
                )
            if baseline_view.kind == "retrieval" or candidate_view.kind == "retrieval":
                return _diagnosis(
                    "retrieval",
                    "the first aligned retrieval operation has a different span kind",
                    baseline,
                    candidate,
                    baseline_span=baseline_span,
                    candidate_span=candidate_span,
                )
            return _diagnosis(
                "indeterminate",
                "observable span kinds could not be aligned",
                baseline,
                candidate,
                baseline_span=baseline_span,
                candidate_span=candidate_span,
            )

        if baseline_view.kind in {"tool", "tool_result"}:
            if baseline_view.name != candidate_view.name:
                return _diagnosis(
                    "tool_selection",
                    "the first tool name differs between baseline and candidate",
                    baseline,
                    candidate,
                    baseline_span=baseline_span,
                    candidate_span=candidate_span,
                    evidence=[
                        {
                            "field": "tool_name",
                            "baseline": baseline_view.name,
                            "candidate": candidate_view.name,
                            "index": index,
                        }
                    ],
                )
            if _stable(baseline_view.arguments) != _stable(candidate_view.arguments):
                return _diagnosis(
                    "tool_arguments",
                    "the first shared tool receives different arguments",
                    baseline,
                    candidate,
                    baseline_span=baseline_span,
                    candidate_span=candidate_span,
                    evidence=[
                        {
                            "field": "tool_arguments",
                            "baseline": baseline_view.arguments,
                            "candidate": candidate_view.arguments,
                            "index": index,
                        }
                    ],
                )
            if (
                baseline_span.status != candidate_span.status
                or baseline_span.error != candidate_span.error
            ):
                category: AttributionCategory = (
                    "timeout"
                    if _is_timeout(baseline_span, baseline)
                    or _is_timeout(candidate_span, candidate)
                    else "tool_execution"
                )
                return _diagnosis(
                    category,
                    "the first shared tool execution has different status or error evidence",
                    baseline,
                    candidate,
                    baseline_span=baseline_span,
                    candidate_span=candidate_span,
                    evidence=[
                        {
                            "field": "execution",
                            "baseline_status": baseline_span.status,
                            "candidate_status": candidate_span.status,
                            "baseline_error": baseline_span.error,
                            "candidate_error": candidate_span.error,
                            "index": index,
                        }
                    ],
                )
        elif baseline_view.kind == "retrieval":
            baseline_evidence = (baseline_span.input, baseline_span.output, baseline_span.error)
            candidate_evidence = (candidate_span.input, candidate_span.output, candidate_span.error)
            if _stable(baseline_evidence) != _stable(candidate_evidence):
                return _diagnosis(
                    "retrieval",
                    "the first retrieval span has different evidence",
                    baseline,
                    candidate,
                    baseline_span=baseline_span,
                    candidate_span=candidate_span,
                )
        elif (
            baseline_span.status != candidate_span.status
            or baseline_span.error != candidate_span.error
        ):
            category = (
                "timeout"
                if _is_timeout(baseline_span, baseline) or _is_timeout(candidate_span, candidate)
                else "indeterminate"
            )
            return _diagnosis(
                category,
                "a non-tool observable span has different status or error evidence",
                baseline,
                candidate,
                baseline_span=baseline_span,
                candidate_span=candidate_span,
            )

    baseline_agent = next((span for span in baseline.spans if span.kind == "agent"), None)
    candidate_agent = next((span for span in candidate.spans if span.kind == "agent"), None)
    if baseline_agent is None or candidate_agent is None:
        return _diagnosis(
            "indeterminate",
            "aligned observable spans exist but the final Agent output span is missing",
            baseline,
            candidate,
        )
    if _stable(baseline_agent.output) != _stable(candidate_agent.output):
        category = _failed_metric_category(failed_metrics) or "final_answer"
        return _diagnosis(
            category,
            "the aligned trajectory ends with different Agent outputs",
            baseline,
            candidate,
            baseline_span=baseline_agent,
            candidate_span=candidate_agent,
            evidence=[
                {
                    "field": "final_output",
                    "baseline": baseline_agent.output,
                    "candidate": candidate_agent.output,
                }
            ],
        )

    if baseline.status != candidate.status:
        category = (
            "timeout"
            if _is_timeout(None, baseline) or _is_timeout(None, candidate)
            else "indeterminate"
        )
        return _diagnosis(
            category,
            "trace status diverged after aligned spans",
            baseline,
            candidate,
        )

    metric_category = _failed_metric_category(failed_metrics)
    if metric_category == "cost_or_latency":
        baseline_latency = _trace_latency(baseline)
        candidate_latency = _trace_latency(candidate)
        baseline_cost = _trace_cost(baseline)
        candidate_cost = _trace_cost(candidate)
        if (
            baseline_latency is not None
            and candidate_latency is not None
            and baseline_latency != candidate_latency
        ) or (
            baseline_cost is not None
            and candidate_cost is not None
            and baseline_cost != candidate_cost
        ):
            return _diagnosis(
                "cost_or_latency",
                "aligned traces differ in recorded cost or latency evidence",
                baseline,
                candidate,
                evidence=[
                    {
                        "baseline_latency_ms": baseline_latency,
                        "candidate_latency_ms": candidate_latency,
                        "baseline_cost": baseline_cost,
                        "candidate_cost": candidate_cost,
                    }
                ],
            )

    return _diagnosis(
        "indeterminate",
        "traces are aligned but the available evidence does not identify a first error",
        baseline,
        candidate,
    )
