from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from agent_eval import DatasetCase
from agent_eval.telemetry import TelemetrySession
from tests.live import tool_agent
from tests.live.provider_preflight import LiveProviderConfig
from tests.live.tool_agent import LiveToolAgent, ToolAgentProtocolError


def config() -> LiveProviderConfig:
    return LiveProviderConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-secret",
        model="configured-model",
        timeout_seconds=5,
    )


def case() -> DatasetCase:
    return DatasetCase(
        id="case-shipped",
        input={"order_id": "ORDER-1002", "request": "Can I cancel this order?"},
        expected_tools=[
            {
                "name": "check_cancellation_eligibility",
                "arguments": {"order_id": "ORDER-1002"},
            }
        ],
        expected_state={"status": "shipped", "eligible": False},
    )


def completion(
    *,
    request_id: str,
    content: str | None,
    tool_name: str | None = None,
    arguments: str = '{"order_id":"ORDER-1002"}',
    usage: object | None = None,
) -> object:
    calls = []
    finish_reason = "stop"
    if tool_name is not None:
        calls = [
            SimpleNamespace(
                id="call-real-1",
                type="function",
                function=SimpleNamespace(name=tool_name, arguments=arguments),
            )
        ]
        finish_reason = "tool_calls"
    return SimpleNamespace(
        id=request_id,
        model="provider-model-release",
        usage=usage
        or SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=14),
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, tool_calls=calls),
            )
        ],
    )


class FakeCompletions:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> object:
        self.requests.append(kwargs)
        return self.responses.pop(0)


class FakeOpenAI:
    def __init__(self, responses: list[object]) -> None:
        self.completions = FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def install_client(
    monkeypatch: pytest.MonkeyPatch, responses: list[object]
) -> FakeOpenAI:
    client = FakeOpenAI(responses)
    monkeypatch.setattr(tool_agent, "OpenAI", lambda **_: client)
    return client


def test_real_tool_loop_uses_provider_call_and_emits_actual_trajectory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = install_client(
        monkeypatch,
        [
            completion(
                request_id="chatcmpl-tool",
                content=None,
                tool_name="check_cancellation_eligibility",
            ),
            completion(
                request_id="chatcmpl-final",
                content="ORDER-1002 has shipped and is no longer cancellable.",
            ),
        ],
    )
    telemetry = TelemetrySession()
    agent = LiveToolAgent(config())

    with telemetry.tracer.start_as_current_span("acceptance-root") as root:
        root.set_attribute("openinference.span.kind", "AGENT")
        result = agent.run(case())

    payload = telemetry.trace_payload(
        root_span=root,
        experiment_id="experiment-test",
        case_id="case-shipped",
    )
    spans = payload["spans"]
    kinds = [span["kind"] for span in spans]
    tool_span = next(span for span in spans if span["kind"] == "tool")
    result_span = next(span for span in spans if span["kind"] == "tool_result")

    assert len(client.completions.requests) == 2
    assert client.completions.requests[0]["tool_choice"] == "auto"
    second_messages = client.completions.requests[1]["messages"]
    assert second_messages[-1]["role"] == "tool"
    assert "already_in_fulfillment" in second_messages[-1]["content"]
    assert result.output == {
        "answer": "ORDER-1002 has shipped and is no longer cancellable.",
        "order_id": "ORDER-1002",
        "found": True,
        "status": "shipped",
        "eligible": False,
        "reason": "already_in_fulfillment",
    }
    assert result.usage == {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28}
    assert kinds.count("llm") == 2
    assert kinds.count("tool") == 1
    assert kinds.count("tool_result") == 1
    assert tool_span["input"] == {"order_id": "ORDER-1002"}
    assert result_span["output"]["status"] == "shipped"
    assert result_span["parent_span_id"] == tool_span["span_id"]
    assert client.closed is True


def test_agent_rejects_answer_without_model_selected_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_client(
        monkeypatch,
        [completion(request_id="chatcmpl-static", content="A fixed answer.")],
    )

    with pytest.raises(ToolAgentProtocolError, match="without selecting a tool"):
        LiveToolAgent(config()).run(case())


@pytest.mark.parametrize(
    ("tool_name", "arguments", "message"),
    [
        ("delete_order", '{"order_id":"ORDER-1002"}', "unknown"),
        ("lookup_order_status", "not-json", "malformed"),
        ("lookup_order_status", '{"order_id":"../../etc"}', "ORDER-0000"),
    ],
)
def test_agent_rejects_invalid_provider_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    arguments: str,
    message: str,
) -> None:
    install_client(
        monkeypatch,
        [
            completion(
                request_id="chatcmpl-invalid",
                content=None,
                tool_name=tool_name,
                arguments=arguments,
            )
        ],
    )

    with pytest.raises(ToolAgentProtocolError, match=message):
        LiveToolAgent(config()).run(case())


def test_agent_requires_usage_from_every_model_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    missing_usage = SimpleNamespace(prompt_tokens=None, completion_tokens=4, total_tokens=4)
    install_client(
        monkeypatch,
        [
            completion(
                request_id="chatcmpl-no-usage",
                content=None,
                tool_name="lookup_order_status",
                usage=missing_usage,
            )
        ],
    )

    with pytest.raises(ToolAgentProtocolError, match="usage.prompt_tokens"):
        LiveToolAgent(config()).run(case())
