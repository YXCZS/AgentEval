from datetime import UTC, datetime

import pytest

from agent_eval_api.contracts import (
    AgentType,
    CaseExecution,
    DatasetCase,
    EvaluatorType,
    EvaluatorVersion,
    ExecutionStatus,
    ExpectedToolCall,
    RetrievalContext,
    ScoreDirection,
    ScoreStatus,
    Trace,
    TraceSpan,
    TraceSpanKind,
)
from agent_eval_api.evaluation import (
    ADAPTERS,
    EvaluationContext,
    ThirdPartyAdapterError,
    evaluate_adapter,
)


def context(adapter: str, *, metric: str = "metric") -> EvaluationContext:
    now = datetime.now(UTC)
    trace = Trace(
        trace_id="trace-1",
        run_id="run-1",
        case_id="case-1",
        status=ExecutionStatus.COMPLETED,
        spans=[
            TraceSpan(
                span_id="span-1",
                trace_id="trace-1",
                kind=TraceSpanKind.AGENT,
                name="agent",
                status=ExecutionStatus.COMPLETED,
                started_at=now,
                ended_at=now,
            ),
            TraceSpan(
                span_id="span-2",
                trace_id="trace-1",
                parent_span_id="span-1",
                kind=TraceSpanKind.TOOL,
                name="lookup",
                status=ExecutionStatus.COMPLETED,
                started_at=now,
                ended_at=now,
                input={"order_id": "42"},
            ),
        ],
    )
    return EvaluationContext(
        case=DatasetCase(
            id="case-1",
            input="Where is order 42?",
            expected_output="shipped",
            expected_tools=[ExpectedToolCall(name="lookup", arguments={"order_id": "42"})],
            retrieval_context=[RetrievalContext(content="Order 42 has shipped")],
        ),
        execution=CaseExecution(
            id="execution-1",
            run_id="run-1",
            case_id="case-1",
            status=ExecutionStatus.COMPLETED,
            output="Order 42 has shipped",
            tool_calls=[ExpectedToolCall(name="lookup", arguments={"order_id": "42"})],
        ),
        evaluator=EvaluatorVersion(
            id=f"evaluator-{adapter}",
            name=metric,
            version="1.0.0",
            evaluator_type=EvaluatorType.ADAPTER,
            supported_agent_types=[AgentType.RAG, AgentType.TOOL, AgentType.CUSTOM],
            score_min=0,
            score_max=1,
            direction=ScoreDirection.HIGHER_IS_BETTER,
            default_threshold=0.8,
            config={"adapter": adapter, "metric": metric},
        ),
        trace=trace,
    )


@pytest.fixture(autouse=True)
def fixed_adapter_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    for adapter in ADAPTERS.values():
        monkeypatch.setattr(adapter, "version", lambda _context: "test-version")


@pytest.mark.asyncio
async def test_deepeval_adapter_maps_test_case_and_preserves_raw_result() -> None:
    received: dict[str, object] = {}

    def runner(payload: dict[str, object]) -> dict[str, object]:
        received.update(payload)
        return {
            "name": "Task Completion",
            "score": 0.9,
            "success": True,
            "reason": "The trajectory completed the task.",
            "evaluation_model": "judge-v1",
        }

    outcome = (await evaluate_adapter(context("deepeval"), runner))[0]

    assert received["actual_output"] == "Order 42 has shipped"
    assert received["tools_called"] == [
        {"name": "lookup", "arguments": {"order_id": "42"}, "order": None}
    ]
    assert outcome.metric_name == "Task Completion"
    assert outcome.status is ScoreStatus.PASSED
    assert outcome.raw_result["adapter"] == "deepeval"
    assert outcome.raw_result["library_version"] == "test-version"
    assert outcome.raw_result["result"]["evaluation_model"] == "judge-v1"


@pytest.mark.asyncio
async def test_ragas_adapter_maps_rag_fields_and_metric() -> None:
    received: dict[str, object] = {}

    async def runner(payload: dict[str, object]) -> dict[str, object]:
        received.update(payload)
        return {"faithfulness": 0.75, "reason": "One claim lacks context support."}

    outcome = (
        await evaluate_adapter(context("ragas", metric="faithfulness"), runner)
    )[0]

    assert received["retrieved_contexts"] == ["Order 42 has shipped"]
    assert received["response"] == "Order 42 has shipped"
    assert outcome.value == 0.75
    assert outcome.status is ScoreStatus.FAILED
    assert outcome.raw_result["metric"] == "faithfulness"


@pytest.mark.asyncio
async def test_promptfoo_adapter_returns_one_outcome_per_assertion() -> None:
    def runner(_: dict[str, object]) -> dict[str, object]:
        return {
            "componentResults": [
                {"assertion": {"type": "is-json"}, "pass": True, "score": 1},
                {
                    "assertion": {"type": "latency", "threshold": 100},
                    "pass": False,
                    "score": 0,
                    "reason": "Too slow",
                },
            ]
        }

    outcomes = await evaluate_adapter(context("promptfoo"), runner)

    assert len(outcomes) == 2
    assert [outcome.status for outcome in outcomes] == [ScoreStatus.PASSED, ScoreStatus.FAILED]
    assert outcomes[1].explanation == "Too slow"
    assert outcomes[1].raw_result["adapter"] == "promptfoo"


@pytest.mark.asyncio
async def test_agentevals_adapter_maps_trace_trajectory_and_boolean_result() -> None:
    received: dict[str, object] = {}

    def runner(payload: dict[str, object]) -> bool:
        received.update(payload)
        return True

    outcome = (await evaluate_adapter(context("agentevals"), runner))[0]

    trajectory = received["trajectory"]
    assert isinstance(trajectory, list)
    assert [step["kind"] for step in trajectory] == ["agent", "tool"]
    assert outcome.status is ScoreStatus.PASSED
    assert outcome.value == 1
    assert outcome.evidence == [{"trajectory_steps": 2}]


@pytest.mark.asyncio
async def test_unavailable_adapter_is_explicit_and_never_fabricates_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ADAPTERS["deepeval"]
    monkeypatch.setattr(
        adapter,
        "version",
        lambda _context: (_ for _ in ()).throw(
            ThirdPartyAdapterError("deepeval", "adapter_unavailable", "not installed")
        ),
    )

    with pytest.raises(ThirdPartyAdapterError) as raised:
        await evaluate_adapter(context("deepeval"), lambda _: {"score": 1})

    assert raised.value.error_type == "adapter_unavailable"


@pytest.mark.asyncio
async def test_runner_failure_is_isolated_as_adapter_execution_error() -> None:
    def runner(_: dict[str, object]) -> object:
        raise RuntimeError("third-party crash")

    with pytest.raises(ThirdPartyAdapterError) as raised:
        await evaluate_adapter(context("ragas"), runner)

    assert raised.value.error_type == "adapter_execution_error"
    assert "third-party crash" in str(raised.value)
