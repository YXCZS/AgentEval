"""OpenTelemetry capture for one SDK-owned Experiment process."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import StatusCode

ATTRIBUTE_PREFIX = "agent_eval."


class CollectingSpanExporter(SpanExporter):
    def __init__(self) -> None:
        self._spans: list[ReadableSpan] = []
        self._lock = Lock()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            self._spans.extend(spans)
        return SpanExportResult.SUCCESS

    def spans_for_trace(self, trace_id: int) -> list[ReadableSpan]:
        with self._lock:
            return [span for span in self._spans if span.context.trace_id == trace_id]


def _json_attribute(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _read_json_attribute(attributes: dict[str, Any], name: str) -> Any:
    value = attributes.get(name)
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _timestamp(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1_000_000_000, tz=UTC).isoformat()


def _span_kind(attributes: dict[str, Any]) -> str:
    raw = str(attributes.get("openinference.span.kind", "agent")).lower()
    return {
        "chain": "agent",
        "retriever": "retrieval",
    }.get(raw, raw if raw in {"agent", "llm", "tool", "tool_result", "retrieval"} else "agent")


class TelemetrySession:
    def __init__(self) -> None:
        self.exporter = CollectingSpanExporter()
        provider = trace.get_tracer_provider()
        if hasattr(provider, "add_span_processor"):
            provider.add_span_processor(SimpleSpanProcessor(self.exporter))
            self.provider = provider
        else:
            sdk_provider = TracerProvider()
            sdk_provider.add_span_processor(SimpleSpanProcessor(self.exporter))
            trace.set_tracer_provider(sdk_provider)
            self.provider = sdk_provider
        self.tracer = trace.get_tracer("agent-eval-sdk", "0.1.0")

    def force_flush(self, timeout_millis: int = 10000) -> bool:
        force_flush = getattr(self.provider, "force_flush", None)
        return bool(force_flush(timeout_millis=timeout_millis)) if force_flush else True

    def trace_payload(
        self,
        *,
        root_span: Any,
        experiment_id: str,
        case_id: str,
    ) -> dict[str, Any]:
        root_context = root_span.get_span_context()
        public_trace_id = f"{root_context.trace_id:032x}"
        spans = []
        for span in self.exporter.spans_for_trace(root_context.trace_id):
            attributes = dict(span.attributes or {})
            parent_span_id = f"{span.parent.span_id:016x}" if span.parent is not None else None
            status = "failed" if span.status.status_code is StatusCode.ERROR else "completed"
            spans.append(
                {
                    "span_id": f"{span.context.span_id:016x}",
                    "trace_id": public_trace_id,
                    "parent_span_id": parent_span_id,
                    "kind": _span_kind(attributes),
                    "name": span.name,
                    "status": status,
                    "started_at": _timestamp(span.start_time),
                    "ended_at": _timestamp(span.end_time),
                    "input": _read_json_attribute(attributes, "agent_eval.input"),
                    "output": _read_json_attribute(attributes, "agent_eval.output"),
                    "error": _read_json_attribute(attributes, "agent_eval.error"),
                    "usage": _read_json_attribute(attributes, "agent_eval.usage") or {},
                    "attributes": attributes,
                    "extensions": {"otel.start_time_unix_nano": span.start_time},
                }
            )
        return {
            "trace_id": public_trace_id,
            "run_id": experiment_id,
            "case_id": case_id,
            "status": "failed" if any(span["status"] == "failed" for span in spans) else "completed",
            "spans": spans,
            "scores": [],
            "source": "sdk",
            "extensions": {"agent_eval.telemetry": "opentelemetry"},
        }

    @staticmethod
    def set_json_attribute(span: Any, name: str, value: Any) -> None:
        span.set_attribute(name, _json_attribute(value))
