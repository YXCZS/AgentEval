import json
from pathlib import Path

from agent_eval_api.contracts import ExecutionStatus, TraceSpanKind
from agent_eval_api.trace_normalization import normalize_trace_payload

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_openinference_fields_are_normalized_and_extensions_are_preserved() -> None:
    trace = normalize_trace_payload(
        {
            "trace_id": "external-trace-1",
            "status": "OK",
            "vendor.trace": {"session": "s-1"},
            "spans": [
                {
                    "span_id": "llm-span",
                    "name": "chat.completions",
                    "startTimeUnixNano": "1720000000000000000",
                    "endTimeUnixNano": "1720000001000000000",
                    "status": {"code": "OK"},
                    "vendor.span": "keep-me",
                    "attributes": {
                        "openinference.span.kind": "LLM",
                        "input.value": '{"question":"Where is order 42?"}',
                        "output.value": "shipped",
                        "gen_ai.request.model": "gpt-test",
                        "vendor.attribute": {"attempt": 1},
                    },
                }
            ],
        },
        source="openinference",
    )

    span = trace.spans[0]
    assert trace.trace_id == "external-trace-1"
    assert trace.source == "openinference"
    assert trace.extensions["vendor.trace"] == {"session": "s-1"}
    assert span.kind is TraceSpanKind.LLM
    assert span.status is ExecutionStatus.COMPLETED
    assert span.input == {"question": "Where is order 42?"}
    assert span.attributes["gen_ai.request.model"] == "gpt-test"
    assert span.attributes["vendor.attribute"] == {"attempt": 1}
    assert span.extensions["vendor.span"] == "keep-me"


def test_otlp_resource_spans_are_flattened_with_resource_attributes() -> None:
    trace = normalize_trace_payload(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "order-agent"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "otlp-trace-1",
                                    "spanId": "tool-span",
                                    "name": "search_order",
                                    "startTimeUnixNano": "1720000000000000000",
                                    "attributes": [
                                        {
                                            "key": "gen_ai.operation.name",
                                            "value": {"stringValue": "execute_tool"},
                                        }
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        },
        source="otlp",
    )

    assert trace.trace_id == "otlp-trace-1"
    assert trace.spans[0].kind is TraceSpanKind.TOOL
    assert trace.spans[0].attributes["service.name"] == "order-agent"


def test_openinference_fixture_covers_supported_span_kinds_and_evidence() -> None:
    payload = json.loads((FIXTURES / "openinference_trace.json").read_text())

    trace = normalize_trace_payload(payload, source="openinference")
    spans = {span.span_id: span for span in trace.spans}

    assert {span.kind for span in spans.values()} == {
        TraceSpanKind.AGENT,
        TraceSpanKind.LLM,
        TraceSpanKind.TOOL,
        TraceSpanKind.TOOL_RESULT,
        TraceSpanKind.RETRIEVAL,
        TraceSpanKind.GUARDRAIL,
        TraceSpanKind.EVALUATOR,
    }
    assert spans["llm-span"].input[0]["role"] == "user"
    assert spans["llm-span"].usage == {
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
    }
    assert spans["llm-span"].cost == 0.004
    assert spans["tool-result-span"].output == {"status": "shipped"}
    assert spans["guardrail-span"].status is ExecutionStatus.FAILED
    assert spans["guardrail-span"].error == {
        "message": "policy check failed",
        "type": "PolicyViolation",
    }
    assert trace.extensions["vendor.trace"]["tenant"] == "fixture"
    assert spans["agent-span"].extensions["vendor.agent"] == "preserve-me"
