import json

import httpx
import pytest

from agent_eval_api.contracts import (
    AgentType,
    CaseExecution,
    DatasetCase,
    EvaluatorType,
    EvaluatorVersion,
    ExecutionStatus,
    ExternalJudgeResponse,
    ScoreDirection,
    ScoreStatus,
)
from agent_eval_api.evaluation import (
    EvaluationContext,
    ExternalJudgeConfig,
    ExternalProtocolError,
    evaluate_llm_judge,
)

SIGNING_SECRET = "external-signing-secret"


def context(
    name: str = "answer_quality",
    *,
    criteria: list[str] | None = None,
    rubric: str | None = None,
    threshold: float | None = 0.8,
    score_min: float | None = 0.0,
    score_max: float | None = 1.0,
) -> EvaluationContext:
    return EvaluationContext(
        case=DatasetCase(
            id="case-1",
            input={"question": "Where is order 42?"},
            expected_output="It has shipped.",
            criteria=criteria or [],
        ),
        execution=CaseExecution(
            id="execution-1",
            run_id="run-1",
            case_id="case-1",
            status=ExecutionStatus.COMPLETED,
            output="Order 42 has shipped.",
            trace_id="trace-1",
        ),
        evaluator=EvaluatorVersion(
            id="judge-1",
            name=name,
            version="1.0.0",
            evaluator_type=EvaluatorType.LLM_JUDGE,
            supported_agent_types=[AgentType.RAG, AgentType.TOOL, AgentType.CUSTOM],
            score_min=score_min,
            score_max=score_max,
            direction=ScoreDirection.HIGHER_IS_BETTER,
            default_threshold=threshold,
            rubric=rubric,
            evaluator_connection_id="evaluator-connection-1",
        ),
    )


def judge_config(**overrides: object) -> ExternalJudgeConfig:
    values: dict[str, object] = {
        "connection_id": "evaluator-connection-1",
        "endpoint": "https://judge.example.test/score",
        "max_retries": 0,
        "retry_backoff_seconds": 0,
    }
    values.update(overrides)
    return ExternalJudgeConfig(**values)  # type: ignore[arg-type]


def response(
    *,
    score: float = 0.9,
    passed: bool | None = True,
) -> dict[str, object]:
    body: dict[str, object] = {
        "score": score,
        "label": "good",
        "explanation": "The answer matches the reference.",
        "evidence": [{"statement": "The order is reported as shipped."}],
        "trace_id": "trace-1",
        "provenance": {
            "source": "external_judge",
            "evaluator_version": "answer_quality@1.0.0",
            "model": "judge-model",
            "model_release": "judge-2026-01",
            "rubric_version": "rubric-3",
            "prompt_template_version": "template-2",
        },
    }
    if passed is not None:
        body["passed"] = passed
    return body


@pytest.mark.asyncio
async def test_external_judge_sends_normalized_request_and_persists_evidence() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Agent-Eval-Signature"].startswith("v1=")
        assert "Authorization" not in request.headers
        body = json.loads(request.content)
        assert body["run_id"] == "run-1"
        assert body["case_id"] == "case-1"
        assert body["trace_id"] == "trace-1"
        assert body["metric_name"] == "answer_quality"
        assert body["evaluator_version"] == "answer_quality@1.0.0"
        assert body["rubric"]
        assert body["input"] == {"question": "Where is order 42?"}
        assert body["expected_output"] == "It has shipped."
        assert body["actual_output"] == "Order 42 has shipped."
        assert body["tool_calls"] == []
        assert body["metadata"]["source"] == "offline-experiment"
        return httpx.Response(200, json=response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        outcome = (
            await evaluate_llm_judge(
                context(),
                judge_config(),
                signing_secret=SIGNING_SECRET,
                client=client,
            )
        )[0]

    assert outcome.status is ScoreStatus.PASSED
    assert outcome.value == 0.9
    assert outcome.passed is True
    assert outcome.evidence == [{"statement": "The order is reported as shipped."}]
    assert outcome.provenance is not None
    assert outcome.provenance.model == "judge-model"
    assert outcome.provenance.model_release == "judge-2026-01"
    assert outcome.provenance.rubric_version == "rubric-3"
    assert outcome.provenance.prompt_template_version == "template-2"
    assert outcome.provenance.protocol == "signed_http_json_v1"
    assert outcome.provenance.connection_id == "evaluator-connection-1"
    assert outcome.raw_response == response()
    assert outcome.raw_result["protocol"] == "signed_http_json_v1"
    assert outcome.raw_result["response"]["provenance"]["model_release"] == "judge-2026-01"
    assert SIGNING_SECRET not in json.dumps(outcome.raw_result)


@pytest.mark.asyncio
async def test_explicit_failed_decision_is_preserved() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response(score=0.2, passed=False))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        outcome = (
            await evaluate_llm_judge(
                context(), judge_config(), signing_secret=SIGNING_SECRET, client=client
            )
        )[0]

    assert outcome.status is ScoreStatus.FAILED
    assert outcome.value == 0.2
    assert outcome.passed is False


@pytest.mark.asyncio
async def test_missing_passed_is_thresholded_by_the_platform() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response(passed=None))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        outcome = (
            await evaluate_llm_judge(
                context(), judge_config(), signing_secret=SIGNING_SECRET, client=client
            )
        )[0]

    assert outcome.status is ScoreStatus.PASSED
    assert outcome.passed is True


@pytest.mark.asyncio
async def test_missing_passed_and_threshold_is_incomplete() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response(passed=None))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        outcome = (
            await evaluate_llm_judge(
                context(threshold=None),
                judge_config(),
                signing_secret=SIGNING_SECRET,
                client=client,
            )
        )[0]

    assert outcome.status is ScoreStatus.MISSING
    assert outcome.passed is None
    assert "no evaluator threshold" in (outcome.explanation or "")


@pytest.mark.asyncio
async def test_score_outside_evaluator_range_is_a_protocol_error() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response(score=1.1))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ExternalProtocolError) as raised:
            await evaluate_llm_judge(
                context(), judge_config(), signing_secret=SIGNING_SECRET, client=client
            )

    assert raised.value.error_type == "protocol_error"


@pytest.mark.asyncio
async def test_external_judge_retries_transient_service_errors() -> None:
    attempts = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json=response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        outcome = (
            await evaluate_llm_judge(
                context(),
                judge_config(max_retries=2),
                signing_secret=SIGNING_SECRET,
                client=client,
            )
        )[0]

    assert attempts == 3
    assert outcome.raw_result["attempts"] == 3


@pytest.mark.asyncio
async def test_malformed_external_judge_response_is_not_retried() -> None:
    attempts = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"score": 0.9})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ExternalProtocolError) as raised:
            await evaluate_llm_judge(
                context(),
                judge_config(max_retries=5),
                signing_secret=SIGNING_SECRET,
                client=client,
            )

    assert raised.value.error_type == "protocol_error"
    assert raised.value.attempts == 1
    assert attempts == 1


def test_external_response_contract_requires_provenance() -> None:
    with pytest.raises(ValueError):
        ExternalJudgeResponse(
            score=0.9,
            explanation="missing provenance",
        )
