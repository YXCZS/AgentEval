from datetime import UTC, datetime, timedelta

from agent_eval_api.attribution import attribute_first_error
from agent_eval_api.db import TraceRecord, TraceSpanRecord


def _trace(*, tool_name: str, arguments: dict[str, str], trace_id: str = "trace") -> TraceRecord:
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    trace = TraceRecord(
        id=f"record-{trace_id}",
        project_id="project-1",
        trace_id=trace_id,
        status="completed",
        source="test",
        extensions={},
    )
    trace.spans = [
        TraceSpanRecord(
            trace_id=trace.id,
            span_id="agent",
            kind="agent",
            name="agent",
            status="completed",
            started_at=started_at,
            ended_at=started_at + timedelta(seconds=1),
            output={"answer": "same"},
        ),
        TraceSpanRecord(
            trace_id=trace.id,
            span_id="tool-1",
            kind="tool",
            name=tool_name,
            status="completed",
            started_at=started_at + timedelta(milliseconds=100),
            ended_at=started_at + timedelta(milliseconds=200),
            input=arguments,
            attributes={"tool.name": tool_name},
        ),
    ]
    return trace


def test_attribution_locates_first_divergent_tool_span() -> None:
    baseline = _trace(tool_name="search_order", arguments={"order_id": "42"}, trace_id="base")
    candidate = _trace(tool_name="cancel_order", arguments={"order_id": "42"}, trace_id="cand")

    result = attribute_first_error(baseline, candidate)

    assert result.category == "tool_selection"
    assert result.baseline_trace_id == "base"
    assert result.candidate_trace_id == "cand"
    assert result.baseline_span_id == "tool-1"
    assert result.candidate_span_id == "tool-1"
    assert result.evidence[0]["field"] == "tool_name"


def test_attribution_distinguishes_tool_argument_divergence() -> None:
    baseline = _trace(tool_name="search_order", arguments={"order_id": "42"}, trace_id="base")
    candidate = _trace(tool_name="search_order", arguments={"order_id": "43"}, trace_id="cand")

    result = attribute_first_error(baseline, candidate)

    assert result.category == "tool_arguments"
    assert result.candidate_span_id == "tool-1"
    assert result.evidence[0]["field"] == "tool_arguments"


def test_attribution_is_indeterminate_without_aligned_telemetry() -> None:
    candidate = _trace(tool_name="search_order", arguments={"order_id": "42"}, trace_id="cand")

    result = attribute_first_error(None, candidate)

    assert result.category == "indeterminate"
    assert result.baseline_trace_id is None
    assert result.candidate_trace_id == "cand"
