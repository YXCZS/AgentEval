import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from agent_eval_api.contracts import (
    AgentType,
    CaseExecution,
    DatasetCase,
    EvaluatorType,
    EvaluatorVersion,
    ExecutionStatus,
    ScoreDirection,
    ScoreStatus,
)
from agent_eval_api.evaluation import (
    EvaluationContext,
    ManagedJudgeConfig,
    ManagedJudgeError,
    ManagedJudgeRetryableError,
    evaluate_managed_judge,
)
from agent_eval_api.evaluation.dispatch import dispatch_managed_judge

SECRET = "sk-private-managed-judge-key"


class FakeCompletions:
    def __init__(self, result: object) -> None:
        self.result = result
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> object:
        self.requests.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeClient:
    def __init__(self, result: object) -> None:
        self.completions = FakeCompletions(result)
        self.chat = SimpleNamespace(completions=self.completions)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def context(
    *,
    threshold: float | None = 0.8,
    score_min: float | None = 0.0,
    score_max: float | None = 1.0,
    direction: ScoreDirection = ScoreDirection.HIGHER_IS_BETTER,
) -> EvaluationContext:
    return EvaluationContext(
        case=DatasetCase(
            id="case-1",
            input={"question": "Where is order 42?"},
            expected_output="It has shipped.",
            criteria=["Use the supplied order status."],
        ),
        execution=CaseExecution(
            id="execution-1",
            run_id="run-1",
            case_id="case-1",
            status=ExecutionStatus.COMPLETED,
            output={"answer": "Order 42 has shipped."},
            trace_id="trace-1",
        ),
        evaluator=EvaluatorVersion(
            id="judge-1",
            name="answer_quality",
            version="1.0.0",
            evaluator_type=EvaluatorType.LLM_JUDGE,
            supported_agent_types=[AgentType.RAG, AgentType.TOOL, AgentType.CUSTOM],
            score_min=score_min,
            score_max=score_max,
            direction=direction,
            default_threshold=threshold,
            rubric="Score factual correctness from 0 to 1.",
            provider_connection_id="provider-connection-1",
        ),
    )


def config(**overrides: object) -> ManagedJudgeConfig:
    values: dict[str, object] = {
        "base_url": "https://api.deepseek.com/v1/",
        "model": "deepseek-chat",
        "prompt_template": (
            "Input={{ input | json }}\nExpected={{ expected_output | json }}\n"
            "Actual={{ actual_output | json }}\nRubric={{ rubric }}"
        ),
        "output_schema": {
            "type": "object",
            "required": ["score"],
            "properties": {
                "score": {"type": "number"},
                "explanation": {"type": "string"},
                "evidence": {"type": "array"},
                "label": {"type": "string"},
                "passed": {"type": "boolean"},
            },
        },
        "sampling_parameters": {"temperature": 0.0, "seed": 7},
        "default_parameters": {
            "max_tokens": 200,
            "pricing": {
                "input_per_million_tokens": 2,
                "cached_input_per_million_tokens": 0.5,
                "output_per_million_tokens": 8,
                "currency": "USD",
            },
        },
        "timeout_seconds": 12.0,
    }
    values.update(overrides)
    return ManagedJudgeConfig(**values)  # type: ignore[arg-type]


def response(
    content: object | str | None = None,
    *,
    usage: object | None = ...,
) -> SimpleNamespace:
    if content is None:
        content = {
            "score": 0.9,
            "label": "good",
            "passed": True,
            "explanation": "The answer matches the reference.",
            "evidence": [{"statement": "The order is reported as shipped."}],
        }
    text = content if isinstance(content, str) else json.dumps(content)
    if usage is ...:
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=40,
            total_tokens=140,
            prompt_tokens_details={"cached_tokens": 20},
        )
    return SimpleNamespace(
        id="provider-request-1",
        model="deepseek-chat",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text),
                finish_reason="stop",
            )
        ],
        usage=usage,
    )


def test_dispatch_sends_only_the_score_identifier_to_the_managed_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class Producer:
        def send_task(self, name: str, **kwargs: object) -> SimpleNamespace:
            calls.append({"name": name, **kwargs})
            return SimpleNamespace(id="celery-task-1")

    monkeypatch.setattr(
        "agent_eval_api.evaluation.dispatch._producer",
        lambda redis_url: Producer(),
    )

    task_id = dispatch_managed_judge(
        redis_url="redis://queue.example.test/0",
        score_id="score-1",
    )

    assert task_id == "celery-task-1"
    assert calls == [
        {
            "name": "agent_eval.execute_managed_judge",
            "args": ["score-1"],
            "queue": "managed-judge",
        }
    ]
    serialized = json.dumps(calls)
    assert SECRET not in serialized
    assert "ciphertext" not in serialized
    assert "actual_output" not in serialized


def test_managed_judge_sends_structured_request_and_records_usage_cost() -> None:
    client = FakeClient(response())

    outcome = evaluate_managed_judge(context(), config(), api_key=SECRET, client=client)

    assert client.closed is False
    request = client.completions.requests[0]
    assert request["model"] == "deepseek-chat"
    assert request["response_format"] == {"type": "json_object"}
    assert request["stream"] is False
    assert request["temperature"] == 0.0
    assert request["seed"] == 7
    assert request["max_tokens"] == 200
    assert "pricing" not in request
    assert "Order 42 has shipped" in request["messages"][1]["content"]
    assert outcome.status is ScoreStatus.PASSED
    assert outcome.value == 0.9
    assert outcome.provenance is not None
    assert outcome.provenance.source == "platform_provider"
    assert outcome.provenance.protocol == "openai_compatible_chat_completions"
    assert outcome.provenance.connection_id == "provider-connection-1"
    assert outcome.provenance.model == "deepseek-chat"
    assert outcome.usage == {
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "total_tokens": 140,
        "prompt_tokens_details": {"cached_tokens": 20},
    }
    assert outcome.cost == {
        "amount": 0.00049,
        "currency": "USD",
        "calculation": "configured_token_rates",
        "input_tokens": 80,
        "cached_input_tokens": 20,
        "output_tokens": 40,
    }
    assert SECRET not in json.dumps(outcome.raw_result)
    assert SECRET not in json.dumps(outcome.raw_response)


def test_managed_judge_closes_only_an_internally_created_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = FakeClient(response())
    constructor_arguments: dict[str, object] = {}

    def fake_openai(**kwargs: object) -> FakeClient:
        constructor_arguments.update(kwargs)
        return created

    monkeypatch.setattr("agent_eval_api.evaluation.managed_provider.OpenAI", fake_openai)

    evaluate_managed_judge(context(), config(), api_key=SECRET)

    assert created.closed is True
    assert constructor_arguments == {
        "api_key": SECRET,
        "base_url": "https://api.deepseek.com/v1",
        "timeout": 12.0,
        "max_retries": 0,
    }


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("not-json", "malformed JSON"),
        ({"score": 0.9}, "explanation must be a non-empty string"),
        (
            {"score": 0.9, "explanation": "ok", "evidence": "wrong"},
            "evidence must be an array of objects",
        ),
        (
            {"score": 0.9, "explanation": "ok", "label": 1},
            "label must be a string",
        ),
        (
            {"score": 0.9, "explanation": "ok", "passed": "yes"},
            "passed field must be a boolean",
        ),
    ],
)
def test_managed_judge_rejects_malformed_structured_fields(
    content: object,
    message: str,
) -> None:
    with pytest.raises(ManagedJudgeError, match=message):
        evaluate_managed_judge(
            context(),
            config(output_schema={"type": "object", "required": ["score"]}),
            api_key=SECRET,
            client=FakeClient(response(content)),
        )


def test_managed_judge_enforces_the_configured_json_schema() -> None:
    schema = {
        "type": "object",
        "required": ["score", "verdict"],
        "properties": {
            "score": {"type": "number"},
            "verdict": {"type": "string"},
        },
    }

    with pytest.raises(ManagedJudgeError, match="does not match"):
        evaluate_managed_judge(
            context(),
            config(output_schema=schema),
            api_key=SECRET,
            client=FakeClient(response()),
        )


@pytest.mark.parametrize("score", [-0.01, 1.01])
def test_managed_judge_rejects_scores_outside_evaluator_range(score: float) -> None:
    with pytest.raises(ManagedJudgeError, match="score is (below|above)"):
        evaluate_managed_judge(
            context(),
            config(),
            api_key=SECRET,
            client=FakeClient(
                response({"score": score, "explanation": "outside range"})
            ),
        )


@pytest.mark.parametrize(
    "usage",
    [
        None,
        SimpleNamespace(prompt_tokens=True, completion_tokens=1, total_tokens=2),
        SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=2),
    ],
)
def test_managed_judge_requires_valid_provider_usage(usage: object | None) -> None:
    with pytest.raises(ManagedJudgeError, match="usage"):
        evaluate_managed_judge(
            context(),
            config(),
            api_key=SECRET,
            client=FakeClient(response(usage=usage)),
        )


def test_managed_judge_applies_directional_threshold_without_explicit_passed() -> None:
    outcome = evaluate_managed_judge(
        context(threshold=0.2, direction=ScoreDirection.LOWER_IS_BETTER),
        config(),
        api_key=SECRET,
        client=FakeClient(
            response({"score": 0.1, "explanation": "Within the error budget."})
        ),
    )

    assert outcome.status is ScoreStatus.PASSED
    assert outcome.passed is True


def _status_error(error_type: type[Exception], status_code: int) -> Exception:
    request = httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions")
    response_value = httpx.Response(status_code, request=request)
    return error_type(f"upstream rejected {SECRET}", response=response_value, body=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "error",
    [
        APITimeoutError(
            request=httpx.Request(
                "POST", "https://api.deepseek.com/v1/chat/completions"
            )
        ),
        APIConnectionError(
            request=httpx.Request(
                "POST", "https://api.deepseek.com/v1/chat/completions"
            )
        ),
        _status_error(RateLimitError, 429),
        _status_error(InternalServerError, 503),
    ],
)
def test_transient_provider_errors_are_retryable_and_secret_safe(error: Exception) -> None:
    with pytest.raises(ManagedJudgeRetryableError) as raised:
        evaluate_managed_judge(
            context(),
            config(),
            api_key=SECRET,
            client=FakeClient(error),
        )

    assert str(raised.value) == "managed Judge provider is temporarily unavailable"
    assert SECRET not in str(raised.value)


@pytest.mark.parametrize(
    "error",
    [
        _status_error(AuthenticationError, 401),
        _status_error(BadRequestError, 400),
    ],
)
def test_permanent_provider_errors_are_not_retryable_and_are_secret_safe(
    error: Exception,
) -> None:
    with pytest.raises(ManagedJudgeError) as raised:
        evaluate_managed_judge(
            context(),
            config(),
            api_key=SECRET,
            client=FakeClient(error),
        )

    assert not isinstance(raised.value, ManagedJudgeRetryableError)
    assert str(raised.value) == "managed Judge provider rejected the request"
    assert SECRET not in str(raised.value)


def test_invalid_prompt_template_fails_before_the_provider_call() -> None:
    client = FakeClient(response())

    with pytest.raises(ManagedJudgeError, match="prompt template is invalid"):
        evaluate_managed_judge(
            context(),
            config(prompt_template="{{ missing_value }}"),
            api_key=SECRET,
            client=client,
        )

    assert client.completions.requests == []
