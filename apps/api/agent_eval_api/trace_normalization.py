"""OpenTelemetry and OpenInference payload mapping into the canonical Trace model."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agent_eval_api.contracts import ExecutionStatus, Trace, TraceSpan, TraceSpanKind

_TRACE_FIELDS = {
    "trace_id",
    "traceId",
    "id",
    "run_id",
    "case_id",
    "status",
    "source",
    "spans",
    "resourceSpans",
    "extensions",
}
_SPAN_FIELDS = {
    "trace_id",
    "traceId",
    "span_id",
    "spanId",
    "id",
    "parent_span_id",
    "parentSpanId",
    "parent_id",
    "name",
    "status",
    "start_time",
    "started_at",
    "startTimeUnixNano",
    "end_time",
    "ended_at",
    "endTimeUnixNano",
    "kind",
    "input",
    "output",
    "error",
    "usage",
    "cost",
    "attributes",
    "extensions",
}


def normalize_trace_payload(payload: dict[str, Any], *, source: str) -> Trace:
    """Normalize the common OTel/OpenInference fields and retain everything else."""

    raw_spans = _collect_spans(payload)
    trace_id = _string_or_none(
        payload.get("trace_id") or payload.get("traceId") or payload.get("id")
    )
    if trace_id is None and raw_spans:
        first_span = raw_spans[0][0]
        trace_id = _string_or_none(first_span.get("trace_id") or first_span.get("traceId"))
    trace_id = trace_id or str(uuid4())
    spans = [_normalize_span(raw_span, trace_id, inherited) for raw_span, inherited in raw_spans]
    extensions = (
        dict(payload["extensions"])
        if isinstance(payload.get("extensions"), dict)
        else {}
    )
    extensions.update(
        {key: value for key, value in payload.items() if key not in _TRACE_FIELDS}
    )
    return Trace(
        trace_id=trace_id,
        run_id=_string_or_none(payload.get("run_id")),
        case_id=_string_or_none(payload.get("case_id")),
        status=_execution_status(payload.get("status")),
        spans=spans,
        source=source,
        extensions=extensions,
    )


def _collect_spans(payload: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    direct_spans = payload.get("spans")
    if isinstance(direct_spans, list):
        return [(span, {}) for span in direct_spans if isinstance(span, dict)]

    spans: list[tuple[dict[str, Any], dict[str, Any]]] = []
    resource_spans = payload.get("resourceSpans")
    if not isinstance(resource_spans, list):
        return spans
    for resource_span in resource_spans:
        if not isinstance(resource_span, dict):
            continue
        resource = resource_span.get("resource")
        resource = resource if isinstance(resource, dict) else {}
        resource_attributes = _attributes(resource.get("attributes"))
        resource_extensions = _unknown_fields(resource, {"attributes"})
        for scope_span in resource_span.get("scopeSpans", []):
            if not isinstance(scope_span, dict):
                continue
            inherited_extensions: dict[str, Any] = {}
            if resource_extensions:
                inherited_extensions["otlp.resource"] = resource_extensions
            scope_extensions = _unknown_fields(scope_span, {"spans"})
            if scope_extensions:
                inherited_extensions["otlp.scope"] = scope_extensions
            for span in scope_span.get("spans", []):
                if not isinstance(span, dict):
                    continue
                span_copy = dict(span)
                span_copy["attributes"] = resource_attributes | _attributes(span.get("attributes"))
                spans.append((span_copy, inherited_extensions))
    return spans


def _normalize_span(
    raw: dict[str, Any], trace_id: str, inherited_extensions: dict[str, Any] | None = None
) -> TraceSpan:
    attributes = _attributes(raw.get("attributes"))
    kind = _span_kind(attributes, raw.get("kind"))
    extensions = dict(inherited_extensions or {})
    if isinstance(raw.get("extensions"), dict):
        extensions.update(raw["extensions"])
    extensions.update({key: value for key, value in raw.items() if key not in _SPAN_FIELDS})
    return TraceSpan(
        span_id=_string_or_none(raw.get("span_id") or raw.get("spanId") or raw.get("id"))
        or str(uuid4()),
        trace_id=trace_id,
        parent_span_id=_string_or_none(
            raw.get("parent_span_id") or raw.get("parentSpanId") or raw.get("parent_id")
        ),
        kind=kind,
        name=_string_or_none(raw.get("name")) or kind.value,
        status=_execution_status(raw.get("status")),
        started_at=_timestamp(
            raw.get("started_at") or raw.get("start_time") or raw.get("startTimeUnixNano")
        ),
        ended_at=_optional_timestamp(
            raw.get("ended_at") or raw.get("end_time") or raw.get("endTimeUnixNano")
        ),
        input=_field_or_semantic(
        raw, "input", attributes, "input.value", "gen_ai.input.messages", "llm.input_messages"
            , "tool.call.arguments", "tool.parameters"
        ),
        output=_field_or_semantic(
            raw,
            "output",
            attributes,
            "output.value",
            "gen_ai.output.messages",
            "llm.output_messages",
            "tool.call.result",
            "tool.result",
        ),
        error=_error(raw.get("error"), attributes, raw.get("status")),
        usage=_usage(raw.get("usage"), attributes),
        cost=_cost(raw.get("cost"), attributes),
        attributes=attributes,
        extensions=extensions,
    )


def _attributes(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, list):
        return {}
    attributes: dict[str, Any] = {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            continue
        attributes[item["key"]] = _otlp_value(item.get("value"))
    return attributes


def _otlp_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "doubleValue", "boolValue", "bytesValue"):
        if key in value:
            return value[key]
    if "intValue" in value:
        integer = value["intValue"]
        try:
            return int(integer)
        except (TypeError, ValueError):
            return integer
    if "arrayValue" in value and isinstance(value["arrayValue"], dict):
        values = value["arrayValue"].get("values", [])
        return [_otlp_value(item) for item in values] if isinstance(values, list) else []
    if "kvlistValue" in value and isinstance(value["kvlistValue"], dict):
        return _attributes(value["kvlistValue"].get("values"))
    return value


def _unknown_fields(value: dict[str, Any], known: set[str]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in known}


def _span_kind(attributes: dict[str, Any], raw_kind: Any) -> TraceSpanKind:
    candidate = str(attributes.get("openinference.span.kind") or raw_kind or "").lower()
    if candidate in {"agent", "chain", "workflow"}:
        return TraceSpanKind.AGENT
    if candidate in {"prompt", "template"}:
        return TraceSpanKind.PROMPT
    if candidate in {"llm", "chat", "completion"}:
        return TraceSpanKind.LLM
    if candidate in {"tool", "tool_call"}:
        return TraceSpanKind.TOOL
    if candidate in {"tool_result", "toolresult"}:
        return TraceSpanKind.TOOL_RESULT
    if candidate in {"retriever", "retrieval", "reranker", "embedder"}:
        return TraceSpanKind.RETRIEVAL
    if candidate == "guardrail":
        return TraceSpanKind.GUARDRAIL
    if candidate == "evaluator":
        return TraceSpanKind.EVALUATOR
    operation = str(attributes.get("gen_ai.operation.name", "")).lower()
    if any(token in operation for token in ("agent", "workflow")):
        return TraceSpanKind.AGENT
    if "tool" in operation:
        return TraceSpanKind.TOOL
    if "retriev" in operation or "rerank" in operation or "embed" in operation:
        return TraceSpanKind.RETRIEVAL
    if operation or any(key.startswith("gen_ai.") for key in attributes):
        return TraceSpanKind.LLM
    return TraceSpanKind.AGENT


def _semantic_value(attributes: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in attributes:
            return _maybe_parse_json(attributes[key])
    return None


def _field_or_semantic(
    raw: dict[str, Any], field: str, attributes: dict[str, Any], *keys: str
) -> Any:
    value = raw.get(field)
    return value if value is not None else _semantic_value(attributes, *keys)


def _usage(raw_usage: Any, attributes: dict[str, Any]) -> dict[str, Any]:
    usage = dict(raw_usage) if isinstance(raw_usage, dict) else {}
    aliases = {
        "input_tokens": (
            "gen_ai.usage.input_tokens",
            "gen_ai.usage.prompt_tokens",
            "llm.token_count.prompt",
        ),
        "output_tokens": (
            "gen_ai.usage.output_tokens",
            "gen_ai.usage.completion_tokens",
            "llm.token_count.completion",
        ),
        "total_tokens": ("gen_ai.usage.total_tokens", "llm.token_count.total"),
    }
    for normalized_key, keys in aliases.items():
        if normalized_key not in usage:
            value = _semantic_value(attributes, *keys)
            if value is not None:
                usage[normalized_key] = value
    return usage


def _cost(raw_cost: Any, attributes: dict[str, Any]) -> Any:
    if raw_cost is not None:
        return raw_cost
    return _semantic_value(
        attributes,
        "gen_ai.usage.cost",
        "gen_ai.response.cost",
        "llm.cost",
        "cost.total",
    )


def _maybe_parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _error(raw_error: Any, attributes: dict[str, Any], raw_status: Any) -> dict[str, Any] | None:
    if isinstance(raw_error, dict):
        return raw_error
    if _execution_status(raw_status) is not ExecutionStatus.FAILED:
        return None
    message = attributes.get("error.message") or attributes.get("exception.message")
    error_type = attributes.get("error.type") or attributes.get("exception.type")
    status_message = raw_status.get("message") if isinstance(raw_status, dict) else None
    stacktrace = attributes.get("exception.stacktrace")
    return {
        key: value
        for key, value in {
            "message": message or status_message,
            "type": error_type,
            "stacktrace": stacktrace,
        }.items()
        if value
    }


def _execution_status(raw: Any) -> ExecutionStatus:
    if isinstance(raw, dict):
        raw = raw.get("code") or raw.get("status")
    value = str(raw or "completed").lower()
    if value in {"error", "failed", "failure", "status_code_error"}:
        return ExecutionStatus.FAILED
    if value in {"running", "in_progress"}:
        return ExecutionStatus.RUNNING
    if value in {"cancelled", "canceled"}:
        return ExecutionStatus.CANCELLED
    if value in {"queued", "pending"}:
        return ExecutionStatus.QUEUED
    return ExecutionStatus.COMPLETED


def _timestamp(value: Any) -> datetime:
    return _optional_timestamp(value) or datetime.now(UTC)


def _optional_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        numeric_value = float(value)
        if numeric_value > 10_000_000_000:
            numeric_value /= 1_000_000_000
        return datetime.fromtimestamp(numeric_value, UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
