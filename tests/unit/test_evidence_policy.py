from datetime import UTC, datetime

import pytest

from agent_eval_api.db import (
    DatasetCaseRecord,
    EvaluationRunRecord,
    ExperimentItemAttemptRecord,
    TraceRecord,
    TraceSpanRecord,
)
from agent_eval_api.evaluation_runs import evidence_reasons

NOW = datetime.now(UTC)


def build_evidence(
    policy: str,
    *,
    include_llm: bool = True,
    include_model: bool = True,
    include_usage: bool = True,
    include_tool: bool = True,
    include_tool_result: bool = True,
    include_retrieval: bool = True,
    include_document_ids: bool = True,
) -> tuple[EvaluationRunRecord, ExperimentItemAttemptRecord]:
    case = DatasetCaseRecord(
        id="case-record-1",
        dataset_version_id="version-1",
        case_key="case-1",
        input_json={"question": "status"},
        expected_tools=[{"name": "lookup"}],
        retrieval_context=[{"content": "fact", "document_id": "doc-1"}],
    )
    run = EvaluationRunRecord(
        id="experiment-1",
        project_id="project-1",
        agent_version_id="release-1",
        dataset_version_id="version-1",
        evidence_policy=policy,
        configuration_snapshot={
            "dataset_version": {
                "case_manifest": [
                    {
                        "id": case.id,
                        "case_id": case.case_key,
                        "expected_tools": case.expected_tools,
                        "retrieval_context": case.retrieval_context,
                    }
                ]
            }
        },
    )
    trace = TraceRecord(
        id="trace-record-1",
        project_id="project-1",
        trace_id="trace-1",
        run_id=run.id,
        case_id=case.case_key,
        status="completed",
        source="sdk",
    )
    trace.spans = [
        TraceSpanRecord(
            id="root",
            trace_id=trace.id,
            span_id="root",
            kind="agent",
            name="task",
            status="completed",
            started_at=NOW,
            ended_at=NOW,
        )
    ]
    if include_llm:
        attributes = {}
        if include_model:
            attributes["gen_ai.request.model"] = "real-model"
        if include_usage:
            attributes["agent_eval.usage.present"] = True
        trace.spans.append(
            TraceSpanRecord(
                id="llm",
                trace_id=trace.id,
                span_id="llm",
                parent_span_id="root",
                kind="llm",
                name="chat",
                status="completed",
                started_at=NOW,
                ended_at=NOW,
                attributes=attributes,
            )
        )
    if include_tool:
        trace.spans.append(
            TraceSpanRecord(
                id="tool",
                trace_id=trace.id,
                span_id="tool",
                parent_span_id="root",
                kind="tool",
                name="lookup",
                status="completed",
                started_at=NOW,
                ended_at=NOW,
            )
        )
    if include_tool_result:
        trace.spans.append(
            TraceSpanRecord(
                id="tool-result",
                trace_id=trace.id,
                span_id="tool-result",
                parent_span_id="tool",
                kind="tool_result",
                name="lookup result",
                status="completed",
                started_at=NOW,
                ended_at=NOW,
            )
        )
    if include_retrieval:
        trace.spans.append(
            TraceSpanRecord(
                id="retrieval",
                trace_id=trace.id,
                span_id="retrieval",
                parent_span_id="root",
                kind="retrieval",
                name="retrieve",
                status="completed",
                started_at=NOW,
                ended_at=NOW,
                attributes={
                    "retrieval.document_ids": ["doc-1"] if include_document_ids else []
                },
            )
        )
    item = ExperimentItemAttemptRecord(
        id="item-1",
        experiment=run,
        dataset_case=case,
        repetition=1,
        attempt=1,
        external_run_id="external-1",
        status="completed",
        trace=trace,
    )
    return run, item


@pytest.mark.parametrize(
    "policy",
    [
        "trace_required",
        "llm_required",
        "tool_trajectory_required",
        "rag_trajectory_required",
    ],
)
def test_complete_evidence_satisfies_each_policy(policy: str) -> None:
    run, item = build_evidence(policy)
    assert evidence_reasons(run, item) == []


@pytest.mark.parametrize(
    ("policy", "overrides", "reason"),
    [
        ("trace_required", {"trace": None}, "linked Trace is missing"),
        ("llm_required", {"include_llm": False}, "LLM Span is missing"),
        ("llm_required", {"include_model": False}, "LLM model identity is missing"),
        ("llm_required", {"include_usage": False}, "LLM provider usage marker is missing"),
        ("tool_trajectory_required", {"include_tool": False}, "Tool Span is missing"),
        (
            "tool_trajectory_required",
            {"include_tool_result": False},
            "Tool Result Span is missing",
        ),
        ("rag_trajectory_required", {"include_retrieval": False}, "Retriever Span is missing"),
        (
            "rag_trajectory_required",
            {"include_document_ids": False},
            "retrieved document IDs are missing",
        ),
    ],
)
def test_missing_evidence_returns_explicit_reason(
    policy: str,
    overrides: dict[str, object],
    reason: str,
) -> None:
    trace_override = overrides.pop("trace", "unchanged")
    run, item = build_evidence(policy, **overrides)  # type: ignore[arg-type]
    if trace_override is None:
        item.trace = None
    assert reason in evidence_reasons(run, item)
