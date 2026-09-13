from datetime import UTC, datetime, timedelta

import pytest

from agent_eval_api.contracts import (
    AgentType,
    CaseExecution,
    DatasetCase,
    EvaluatorType,
    EvaluatorVersion,
    ExecutionStatus,
    ExpectedToolCall,
    ScoreDirection,
    ScoreStatus,
    Trace,
    TraceSpan,
    TraceSpanKind,
)
from agent_eval_api.evaluation import (
    EvaluationContext,
    EvaluatorConfigurationError,
    evaluate_deterministic,
)


def context(
    name: str,
    *,
    case: DatasetCase | None = None,
    execution: CaseExecution | None = None,
    config: dict[str, object] | None = None,
    threshold: float | None = 1.0,
    direction: ScoreDirection = ScoreDirection.HIGHER_IS_BETTER,
    trace: Trace | None = None,
) -> EvaluationContext:
    evaluator = EvaluatorVersion(
        id=f"evaluator-{name}",
        name=name,
        version="1.0.0",
        evaluator_type=EvaluatorType.DETERMINISTIC,
        supported_agent_types=[AgentType.TOOL],
        score_min=0,
        score_max=10_000,
        direction=direction,
        default_threshold=threshold,
        config=config or {},
    )
    return EvaluationContext(
        case=case or DatasetCase(id="case-1", input="test"),
        execution=execution
        or CaseExecution(
            id="execution-1",
            run_id="run-1",
            case_id="case-1",
            status=ExecutionStatus.COMPLETED,
        ),
        evaluator=evaluator,
        trace=trace,
    )


def test_task_success_compares_expected_state_as_recursive_subset() -> None:
    case = DatasetCase(
        id="case-1",
        input="cancel",
        expected_state={"order": {"status": "cancelled"}},
    )
    execution = CaseExecution(
        id="execution-1",
        run_id="run-1",
        case_id="case-1",
        status=ExecutionStatus.COMPLETED,
        output={"state": {"order": {"status": "cancelled", "id": "42"}}},
    )

    outcome = evaluate_deterministic(
        context(
            "task_success",
            case=case,
            execution=execution,
            config={"actual_state_path": "state"},
        )
    )[0]

    assert outcome.status is ScoreStatus.PASSED
    assert outcome.value == 1
    assert outcome.evidence[0]["actual_state"] == {
        "order": {"status": "cancelled", "id": "42"}
    }


def test_tool_correctness_scores_selection_and_optional_order() -> None:
    case = DatasetCase(
        id="case-1",
        input="cancel",
        expected_tools=[ExpectedToolCall(name="lookup"), ExpectedToolCall(name="cancel")],
    )
    execution = CaseExecution(
        id="execution-1",
        run_id="run-1",
        case_id="case-1",
        status=ExecutionStatus.COMPLETED,
        tool_calls=[ExpectedToolCall(name="cancel"), ExpectedToolCall(name="lookup")],
    )

    unordered = evaluate_deterministic(
        context("tool_correctness", case=case, execution=execution)
    )[0]
    ordered = evaluate_deterministic(
        context("tool_correctness", case=case, execution=execution, config={"ordered": True})
    )[0]

    assert unordered.status is ScoreStatus.PASSED
    assert unordered.value == 1
    assert ordered.status is ScoreStatus.FAILED
    assert ordered.value == 0


def test_argument_correctness_returns_partial_score_with_per_call_evidence() -> None:
    case = DatasetCase(
        id="case-1",
        input="cancel",
        expected_tools=[
            ExpectedToolCall(name="lookup", arguments={"order_id": "42"}),
            ExpectedToolCall(name="cancel", arguments={"order_id": "42", "reason": "user"}),
        ],
    )
    execution = CaseExecution(
        id="execution-1",
        run_id="run-1",
        case_id="case-1",
        status=ExecutionStatus.COMPLETED,
        tool_calls=[
            ExpectedToolCall(name="lookup", arguments={"order_id": "42", "cached": False}),
            ExpectedToolCall(name="cancel", arguments={"order_id": "43", "reason": "user"}),
        ],
    )

    outcome = evaluate_deterministic(
        context("argument_correctness", case=case, execution=execution)
    )[0]

    assert outcome.status is ScoreStatus.FAILED
    assert outcome.value == 0.5
    assert [item["matched"] for item in outcome.evidence] == [True, False]


def test_policy_compliance_reports_rules_and_does_not_default_missing_policy_to_pass() -> None:
    execution = CaseExecution(
        id="execution-1",
        run_id="run-1",
        case_id="case-1",
        status=ExecutionStatus.COMPLETED,
        output="A refund was issued without approval",
        tool_calls=[ExpectedToolCall(name="refund")],
    )

    failed = evaluate_deterministic(
        context(
            "policy_compliance",
            execution=execution,
            config={
                "forbidden_tools": ["refund"],
                "forbidden_output_patterns": ["without approval"],
            },
        )
    )[0]
    missing = evaluate_deterministic(context("policy_compliance", execution=execution))[0]

    assert failed.status is ScoreStatus.FAILED
    assert {item["rule"] for item in failed.evidence} == {
        "forbidden_tool",
        "forbidden_output_pattern",
    }
    assert missing.status is ScoreStatus.MISSING
    assert missing.passed is None


def test_json_schema_returns_validation_paths_as_evidence() -> None:
    case = DatasetCase(
        id="case-1",
        input="test",
        output_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    )
    execution = CaseExecution(
        id="execution-1",
        run_id="run-1",
        case_id="case-1",
        status=ExecutionStatus.COMPLETED,
        output={"answer": 42},
    )

    outcome = evaluate_deterministic(
        context("json_schema", case=case, execution=execution)
    )[0]

    assert outcome.status is ScoreStatus.FAILED
    assert outcome.evidence[0]["instance_path"] == ["answer"]
    assert "not of type 'string'" in outcome.evidence[0]["message"]


def test_context_recall_reads_canonical_retrieval_span() -> None:
    case = DatasetCase(
        id="case-1",
        input="When will order 42 arrive?",
        retrieval_context=[
            {"document_id": "shipping", "content": "Orders arrive in two days."},
            {"content": "Tracking is available after shipment."},
        ],
    )
    trace = Trace(
        trace_id="trace-1",
        status=ExecutionStatus.COMPLETED,
        spans=[
            TraceSpan(
                span_id="retrieval-1",
                trace_id="trace-1",
                kind=TraceSpanKind.RETRIEVAL,
                name="retrieve_policy",
                status=ExecutionStatus.COMPLETED,
                started_at=datetime.now(UTC),
                output={
                    "documents": [
                        {"document_id": "shipping", "content": "Orders arrive in two days."},
                        {"content": "Tracking is available after shipment."},
                    ]
                },
            )
        ],
    )

    outcome = evaluate_deterministic(
        context("context_recall", case=case, trace=trace)
    )[0]

    assert outcome.status is ScoreStatus.PASSED
    assert outcome.value == 1
    assert outcome.evidence[0]["actual_count"] == 2


def test_context_recall_can_read_external_agent_retrieval_context() -> None:
    case = DatasetCase(
        id="case-1",
        input="test",
        retrieval_context=[{"document_id": "refund", "content": "Refund policy"}],
    )
    trace = Trace(
        trace_id="trace-1",
        status=ExecutionStatus.COMPLETED,
        extensions={
            "agent_eval.external_trace": {
                "retrieval_context": [{"document_id": "refund", "content": "Refund policy"}]
            }
        },
    )

    outcome = evaluate_deterministic(context("retrieval_context", case=case, trace=trace))[0]

    assert outcome.status is ScoreStatus.PASSED
    assert outcome.value == 1


def test_citation_accuracy_reports_precision_recall_and_extra_citations() -> None:
    case = DatasetCase(
        id="case-1",
        input="test",
        retrieval_context=[
            {"document_id": "refund", "content": "Refund policy"},
            {"document_id": "shipping", "content": "Shipping policy"},
        ],
    )
    execution = CaseExecution(
        id="execution-1",
        run_id="run-1",
        case_id="case-1",
        status=ExecutionStatus.COMPLETED,
        output={"answer": "...", "citations": ["refund", "unknown"]},
    )

    outcome = evaluate_deterministic(
        context("citation_accuracy", case=case, execution=execution)
    )[0]

    assert outcome.status is ScoreStatus.FAILED
    assert outcome.value == pytest.approx(0.5)
    assert outcome.evidence[0]["precision"] == pytest.approx(0.5)
    assert outcome.evidence[0]["recall"] == pytest.approx(0.5)


def test_output_format_checks_type_required_fields_and_pattern() -> None:
    case = DatasetCase(id="case-1", input="test")
    execution = CaseExecution(
        id="execution-1",
        run_id="run-1",
        case_id="case-1",
        status=ExecutionStatus.COMPLETED,
        output={"answer": "ok"},
    )

    passed = evaluate_deterministic(
        context(
            "output_format",
            case=case,
            execution=execution,
            config={"format": "object", "required_fields": ["answer"]},
        )
    )[0]
    failed = evaluate_deterministic(
        context(
            "output_format",
            case=case,
            execution=execution,
            config={"format": "string", "pattern": "^ok$"},
        )
    )[0]

    assert passed.status is ScoreStatus.PASSED
    assert failed.status is ScoreStatus.FAILED
    assert failed.evidence[0]["actual_type"] == "dict"


def test_error_rate_is_lower_when_execution_and_trace_are_clean() -> None:
    started_at = datetime.now(UTC)
    clean = CaseExecution(
        id="execution-1",
        run_id="run-1",
        case_id="case-1",
        status=ExecutionStatus.COMPLETED,
        started_at=started_at,
        finished_at=started_at,
    )
    failed = clean.model_copy(
        update={
            "status": ExecutionStatus.FAILED,
            "error_type": "timeout",
            "error_message": "agent timed out",
        }
    )

    clean_outcome = evaluate_deterministic(
        context(
            "error_rate",
            execution=clean,
            threshold=0,
            direction=ScoreDirection.LOWER_IS_BETTER,
        )
    )[0]
    failed_outcome = evaluate_deterministic(
        context(
            "error_rate",
            execution=failed,
            threshold=0,
            direction=ScoreDirection.LOWER_IS_BETTER,
        )
    )[0]

    assert clean_outcome.status is ScoreStatus.PASSED
    assert clean_outcome.value == 0
    assert failed_outcome.status is ScoreStatus.FAILED
    assert failed_outcome.value == 1


def test_latency_and_cost_use_lower_is_better_thresholds() -> None:
    started_at = datetime.now(UTC)
    execution = CaseExecution(
        id="execution-1",
        run_id="run-1",
        case_id="case-1",
        status=ExecutionStatus.COMPLETED,
        usage={"cost": 0.02},
        started_at=started_at,
        finished_at=started_at + timedelta(milliseconds=125),
    )

    latency = evaluate_deterministic(
        context(
            "latency",
            execution=execution,
            config={"max_ms": 200},
            threshold=None,
            direction=ScoreDirection.LOWER_IS_BETTER,
        )
    )[0]
    cost = evaluate_deterministic(
        context(
            "cost",
            execution=execution,
            config={"max_cost": 0.01},
            threshold=None,
            direction=ScoreDirection.LOWER_IS_BETTER,
        )
    )[0]

    assert latency.status is ScoreStatus.PASSED
    assert latency.value == pytest.approx(125)
    assert cost.status is ScoreStatus.FAILED
    assert cost.value == pytest.approx(0.02)


def test_unknown_deterministic_evaluator_is_explicit_configuration_error() -> None:
    with pytest.raises(EvaluatorConfigurationError, match="unknown deterministic evaluator"):
        evaluate_deterministic(context("not_registered"))
