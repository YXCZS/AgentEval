import hashlib
import hmac
import json

import httpx
import pytest

from agent_eval_api.contracts import (
    ExternalJudgeRequest,
    ExternalScoreProvenance,
)
from agent_eval_api.evaluation import (
    ExternalJudgeConfig,
    ExternalProtocolError,
    call_external_judge,
)

SIGNING_SECRET = "external-signing-secret"


def judge_request() -> ExternalJudgeRequest:
    return ExternalJudgeRequest(
        run_id="run-1",
        case_id="case-1",
        trace_id="trace-1",
        metric_name="answer_quality",
        evaluator_version="answer-quality@1.0.0",
        rubric="Score correctness against the reference.",
        input={"question": "Where is order 42?"},
        expected_output="It has shipped.",
        actual_output="Order 42 has shipped.",
        metadata={"source": "offline-experiment"},
    )


def judge_response() -> dict[str, object]:
    return {
        "score": 0.9,
        "passed": True,
        "label": "good",
        "explanation": "The answer matches the reference.",
        "evidence": [{"statement": "The order is reported as shipped."}],
        "trace_id": "trace-1",
        "provenance": {
            "source": "external_judge",
            "evaluator_version": "answer-quality@1.0.0",
            "model": "judge-model",
            "model_release": "judge-2026-01",
            "rubric_version": "rubric-3",
            "prompt_template_version": "template-2",
        },
    }


@pytest.mark.asyncio
async def test_external_judge_sends_normalized_request_and_returns_provenance() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        timestamp = request.headers["X-Agent-Eval-Timestamp"]
        expected = hmac.new(
            SIGNING_SECRET.encode(),
            timestamp.encode("ascii") + b"." + request.content,
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["X-Agent-Eval-Signature"] == f"v1={expected}"
        assert "Authorization" not in request.headers
        body = json.loads(request.content)
        assert body["case_id"] == "case-1"
        assert body["trace_id"] == "trace-1"
        assert body["actual_output"] == "Order 42 has shipped."
        return httpx.Response(200, json=judge_response())

    config = ExternalJudgeConfig(
        connection_id="evaluator-connection-1",
        endpoint="https://judge.example.test/score",
        max_retries=0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await call_external_judge(
            config,
            judge_request(),
            signing_secret=SIGNING_SECRET,
            client=client,
        )

    assert result.response.score == 0.9
    assert result.response.provenance.model_release == "judge-2026-01"
    assert result.response.provenance.protocol == "signed_http_json_v1"
    assert result.response.provenance.connection_id == "evaluator-connection-1"
    assert result.attempts == 1
    assert SIGNING_SECRET not in json.dumps(result.raw_response)


@pytest.mark.asyncio
async def test_external_judge_timeout_is_a_terminal_protocol_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("judge timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ExternalProtocolError) as raised:
            await call_external_judge(
                ExternalJudgeConfig(
                    connection_id="evaluator-connection-1",
                    endpoint="https://judge.example.test/score",
                    max_retries=0,
                ),
                judge_request(),
                signing_secret=SIGNING_SECRET,
                client=client,
            )

    assert raised.value.error_type == "timeout"
    assert raised.value.attempts == 1


@pytest.mark.asyncio
async def test_external_judge_rejects_malformed_payload_without_retry() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"score": 0.9})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ExternalProtocolError) as raised:
            await call_external_judge(
                ExternalJudgeConfig(
                    connection_id="evaluator-connection-1",
                    endpoint="https://judge.example.test/score",
                    max_retries=3,
                ),
                judge_request(),
                signing_secret=SIGNING_SECRET,
                client=client,
            )

    assert raised.value.error_type == "protocol_error"
    assert raised.value.attempts == 1


@pytest.mark.asyncio
async def test_external_judge_does_not_retry_authentication_failure() -> None:
    attempts = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, json={"error": "unauthorized"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ExternalProtocolError) as raised:
            await call_external_judge(
                ExternalJudgeConfig(
                    connection_id="evaluator-connection-1",
                    endpoint="https://judge.example.test/score",
                    max_retries=5,
                ),
                judge_request(),
                signing_secret=SIGNING_SECRET,
                client=client,
            )

    assert raised.value.error_type == "authentication_error"
    assert raised.value.attempts == 1
    assert attempts == 1


@pytest.mark.asyncio
async def test_external_judge_fails_closed_before_network_without_secret() -> None:
    attempts = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json=judge_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ExternalProtocolError) as raised:
            await call_external_judge(
                ExternalJudgeConfig(
                    connection_id="evaluator-connection-1",
                    endpoint="https://judge.example.test/score",
                    max_retries=0,
                ),
                judge_request(),
                signing_secret="",
                client=client,
            )

    assert raised.value.error_type == "authentication_error"
    assert attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provenance_update", "message"),
    [
        ({"source": "platform_provider"}, "provenance source"),
        ({"evaluator_version": "other@2.0.0"}, "evaluator version"),
        (
            {"protocol": "openai_compatible_chat_completions"},
            "provenance protocol",
        ),
        ({"connection_id": "different-connection"}, "provenance connection"),
    ],
)
async def test_external_judge_rejects_spoofed_provenance(
    provenance_update: dict[str, str], message: str
) -> None:
    body = judge_response()
    provenance = dict(body["provenance"])  # type: ignore[arg-type]
    provenance.update(provenance_update)
    body["provenance"] = provenance

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ExternalProtocolError, match=message):
            await call_external_judge(
                ExternalJudgeConfig(
                    connection_id="evaluator-connection-1",
                    endpoint="https://judge.example.test/score",
                    max_retries=0,
                ),
                judge_request(),
                signing_secret=SIGNING_SECRET,
                client=client,
            )


def test_external_score_provenance_rejects_platform_provider_fields() -> None:
    with pytest.raises(ValueError):
        ExternalScoreProvenance(
            evaluator_version="judge@1",
            api_key="fake",  # type: ignore[call-arg]
        )
