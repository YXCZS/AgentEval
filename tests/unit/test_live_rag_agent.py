from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from agent_eval import DatasetCase
from agent_eval.telemetry import TelemetrySession
from tests.live import rag_agent
from tests.live.provider_preflight import LiveEmbeddingConfig, LiveProviderConfig
from tests.live.rag_agent import LiveRagAgent, RagAgentProtocolError


def config() -> LiveProviderConfig:
    return LiveProviderConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-secret",
        model="configured-chat-model",
        timeout_seconds=5,
    )


def case() -> DatasetCase:
    return DatasetCase(
        id="refund-window",
        input={"question": "How long after delivery can I request a refund?"},
        retrieval_context=[
            {
                "document_id": "policy-refund-window",
                "content": "A delivered order can be refunded within 30 calendar days of delivery.",
            }
        ],
    )


def embedding_config() -> LiveEmbeddingConfig:
    return LiveEmbeddingConfig(
        base_url="https://embedding.example.test/v1",
        api_key="embedding-test-secret",
        model="configured-embedding-model",
        timeout_seconds=5,
    )


class FakeEmbeddings:
    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> object:
        self.requests.append(kwargs)
        return self.response


class FakeCompletions:
    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> object:
        self.requests.append(kwargs)
        return self.response


class FakeOpenAI:
    def __init__(self, embedding_response: object, chat_response: object) -> None:
        self.embeddings = FakeEmbeddings(embedding_response)
        self.completions = FakeCompletions(chat_response)
        self.chat = SimpleNamespace(completions=self.completions)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def install_client(monkeypatch: pytest.MonkeyPatch) -> FakeOpenAI:
    chat = SimpleNamespace(
        id="chatcmpl-rag-real",
        model="provider-chat-release",
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=8, total_tokens=28),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"answer":"Refunds are available within 30 days after delivery.",'
                        '"citations":["policy-refund-window"]}'
                    )
                )
            )
        ],
    )
    client = FakeOpenAI(None, chat)

    def dynamic_embedding(**kwargs: Any) -> object:
        client.embeddings.requests.append(kwargs)
        inputs = kwargs.get("input")
        count = len(inputs) if isinstance(inputs, list) else 1
        # index 0 is the query, the rest are reference documents. Give the query
        # and the first document (policy-refund-window) nearly identical vectors
        # so cosine ranking selects it first; other documents are orthogonal.
        data = []
        for index in range(count):
            if index == 0:
                data.append(SimpleNamespace(index=index, embedding=[1.0, 0.0]))
            elif index == 1:
                data.append(SimpleNamespace(index=index, embedding=[0.95, 0.05]))
            else:
                data.append(SimpleNamespace(index=index, embedding=[0.0, 1.0]))
        return SimpleNamespace(data=data, usage=SimpleNamespace(prompt_tokens=11))

    client.embeddings.create = dynamic_embedding  # type: ignore[method-assign]
    monkeypatch.setattr(rag_agent, "OpenAI", lambda **_: client)
    return client


def test_real_rag_task_uses_embedding_cosine_retrieval_and_chat_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = install_client(monkeypatch)
    telemetry = TelemetrySession()
    agent = LiveRagAgent(config(), embedding=embedding_config())

    with telemetry.tracer.start_as_current_span("acceptance-root") as root:
        root.set_attribute("openinference.span.kind", "AGENT")
        result = agent.run(case())

    payload = telemetry.trace_payload(
        root_span=root, experiment_id="experiment-test", case_id="refund-window"
    )
    retrieval = next(span for span in payload["spans"] if span["kind"] == "retrieval")
    llm = next(span for span in payload["spans"] if span["kind"] == "llm")

    assert client.embeddings.requests[0]["model"] == "configured-embedding-model"
    assert len(client.embeddings.requests[0]["input"]) == len(rag_agent.RAG_DOCUMENTS) + 1
    assert client.completions.requests[0]["response_format"] == {"type": "json_object"}
    assert result.output["citations"] == ["policy-refund-window"]
    assert result.output["retrieved_document_ids"][0] == "policy-refund-window"
    assert result.usage == {
        "input_tokens": 31,
        "output_tokens": 8,
        "total_tokens": 39,
        "embedding_input_tokens": 11,
    }
    assert retrieval["attributes"]["retrieval.document_ids"][0] == "policy-refund-window"
    assert retrieval["output"][0]["document_id"] == "policy-refund-window"
    assert retrieval["usage"] == {"input_tokens": 11, "output_tokens": 0, "total_tokens": 11}
    assert llm["usage"] == {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28}
    assert llm["attributes"]["agent_eval.usage.present"] is True
    assert client.closed is True


def test_rag_task_rejects_citations_not_present_in_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = install_client(monkeypatch)
    client.completions.response.choices[0].message.content = (
        '{"answer":"Unsupported","citations":["invented-document"]}'
    )

    with pytest.raises(RagAgentProtocolError, match="was not retrieved"):
        LiveRagAgent(config(), embedding=embedding_config()).run(case())
