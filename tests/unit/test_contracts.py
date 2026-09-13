from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent_eval_api.contracts import (
    AgentType,
    DatasetCase,
    DatasetVersion,
    EvaluationRun,
    EvaluationRunCreateRequest,
    EvaluatorType,
    EvaluatorVersion,
    EvidencePolicy,
    ExperimentExecutionMode,
    ExperimentItemAttempt,
    Score,
    ScoreDirection,
    ScoreStatus,
    Trace,
    TraceSpan,
    TraceSpanKind,
)

NOW = datetime.now(UTC)


@pytest.mark.parametrize("agent_type", list(AgentType))
def test_all_agent_types_are_stable(agent_type: AgentType) -> None:
    assert agent_type in {AgentType.RAG, AgentType.TOOL, AgentType.CUSTOM}


def test_dataset_case_keeps_structured_agent_expectations() -> None:
    case = DatasetCase(
        id="case-1",
        input={"question": "Where is order 42?"},
        variables={"locale": "en-US"},
        expected_output={"status": "shipped"},
        output_schema={"type": "object"},
        expected_tools=[{"name": "search_order", "arguments": {"order_id": "42"}}],
        expected_state={"order_status": "shipped"},
        retrieval_context=[{"content": "Order 42 shipped", "document_id": "doc-1"}],
        messages=[{"role": "user", "content": "Where is order 42?"}],
        metadata={"category": "order"},
    )

    assert case.expected_tools[0].arguments["order_id"] == "42"
    assert case.retrieval_context[0].document_id == "doc-1"


def test_dataset_version_rejects_duplicate_case_ids() -> None:
    case = DatasetCase(id="case-1", input="hello")

    with pytest.raises(ValidationError, match="case ids must be unique"):
        DatasetVersion(
            id="dataset-version-1",
            dataset_id="dataset-1",
            version=1,
            cases=[case, case],
            created_at=NOW,
        )


def test_invalid_enum_and_missing_required_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        DatasetCase(id="case-1")


def test_run_rejects_inconsistent_case_counts() -> None:
    with pytest.raises(ValidationError, match="cannot exceed total_cases"):
        EvaluationRun(
            id="run-1",
            agent_version_id="agent-version-1",
            dataset_version_id="dataset-version-1",
            evaluator_version_ids=["eval-1"],
            total_cases=1,
            completed_cases=1,
            failed_cases=1,
            created_at=NOW,
        )


@pytest.mark.parametrize("mode", list(ExperimentExecutionMode))
@pytest.mark.parametrize("policy", list(EvidencePolicy))
def test_production_experiment_modes_and_evidence_policies_are_stable(
    mode: ExperimentExecutionMode,
    policy: EvidencePolicy,
) -> None:
    request = EvaluationRunCreateRequest(
        agent_version_id="release-1",
        dataset_version_id="dataset-version-1",
        evaluator_version_ids=["evaluator-1"],
        execution_mode=mode,
        evidence_policy=policy,
    )

    assert request.execution_mode is mode
    assert request.evidence_policy is policy


@pytest.mark.parametrize("removed_mode", ["prompt", "demo", "mock", "managed_prompt"])
def test_removed_experiment_modes_are_rejected(removed_mode: str) -> None:
    with pytest.raises(ValidationError):
        EvaluationRunCreateRequest(
            agent_version_id="release-1",
            dataset_version_id="dataset-version-1",
            evaluator_version_ids=["evaluator-1"],
            execution_mode=removed_mode,
        )


def test_experiment_item_attempt_contract_preserves_runtime_evidence() -> None:
    item = ExperimentItemAttempt(
        id="item-1",
        experiment_id="experiment-1",
        case_id="case-1",
        repetition=1,
        attempt=2,
        external_run_id="sdk-process-42",
        status="completed",
        output={"answer": "done"},
        usage={"input_tokens": 8, "output_tokens": 2},
        runtime_metadata={"runtime": "python", "sdk_version": "0.1.0"},
        created_at=NOW,
        started_at=NOW,
        finished_at=NOW,
    )

    assert item.external_run_id == "sdk-process-42"
    assert item.usage["input_tokens"] == 8


@pytest.mark.parametrize(("repetition", "attempt"), [(0, 1), (1, 0)])
def test_experiment_item_attempt_indices_are_positive(
    repetition: int,
    attempt: int,
) -> None:
    with pytest.raises(ValidationError):
        ExperimentItemAttempt(
            id="item-1",
            experiment_id="experiment-1",
            case_id="case-1",
            repetition=repetition,
            attempt=attempt,
            external_run_id="sdk-process-42",
            created_at=NOW,
        )


def test_evaluator_version_rejects_invalid_range() -> None:
    with pytest.raises(ValidationError, match="score_min must be lower"):
        EvaluatorVersion(
            id="eval-1",
            name="Task Success",
            version="1.0.0",
            evaluator_type=EvaluatorType.DETERMINISTIC,
            supported_agent_types=[AgentType.TOOL],
            score_min=1,
            score_max=0,
            direction=ScoreDirection.HIGHER_IS_BETTER,
        )


def test_score_never_defaults_failed_result_to_passed() -> None:
    score = Score(
        id="score-1",
        run_id="run-1",
        case_id="case-1",
        metric_name="task_success",
        evaluator_version_id="eval-1",
        status=ScoreStatus.MISSING,
        direction=ScoreDirection.HIGHER_IS_BETTER,
    )

    assert score.passed is None


def test_trace_requires_matching_span_trace_ids_and_preserves_extensions() -> None:
    trace = Trace(
        trace_id="trace-1",
        status="completed",
        extensions={"vendor.extra": {"attempt": 1}},
        spans=[
            TraceSpan(
                span_id="span-1",
                trace_id="trace-1",
                kind=TraceSpanKind.LLM,
                name="chat.completions",
                status="completed",
                started_at=NOW,
                usage={"input_tokens": 4},
                cost=0.001,
                extensions={"gen_ai.request.temperature": 0.2},
            )
        ],
    )

    assert trace.extensions["vendor.extra"]["attempt"] == 1

    with pytest.raises(ValidationError, match="containing trace"):
        Trace(
            trace_id="trace-1",
            status="completed",
            spans=[
                TraceSpan(
                    span_id="span-1",
                    trace_id="trace-2",
                    kind=TraceSpanKind.AGENT,
                    name="agent",
                    status="completed",
                    started_at=NOW,
                )
            ],
        )


def test_trace_rejects_missing_or_cyclic_span_parents() -> None:
    with pytest.raises(ValidationError, match="parent must exist"):
        Trace(
            trace_id="trace-1",
            status="completed",
            spans=[
                TraceSpan(
                    span_id="span-1",
                    trace_id="trace-1",
                    parent_span_id="missing",
                    kind=TraceSpanKind.AGENT,
                    name="agent",
                    status="completed",
                    started_at=NOW,
                )
            ],
        )

    with pytest.raises(ValidationError, match="parent cycles"):
        Trace(
            trace_id="trace-1",
            status="completed",
            spans=[
                TraceSpan(
                    span_id="span-1",
                    trace_id="trace-1",
                    parent_span_id="span-2",
                    kind=TraceSpanKind.AGENT,
                    name="agent",
                    status="completed",
                    started_at=NOW,
                ),
                TraceSpan(
                    span_id="span-2",
                    trace_id="trace-1",
                    parent_span_id="span-1",
                    kind=TraceSpanKind.LLM,
                    name="llm",
                    status="completed",
                    started_at=NOW,
                ),
            ],
        )
