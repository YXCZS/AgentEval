"""Real embedding retrieval and answer generation for live RAG acceptance."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from agent_eval import DatasetCase, TaskResult
from openai import OpenAI
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from .provider_preflight import (
    LiveEmbeddingConfig,
    LiveProviderConfig,
    _endpoint_origin,
    _safe_error,
)

RAG_SYSTEM_PROMPT = """You answer support questions using only the supplied reference documents.
Return a JSON object with exactly two fields: answer (a concise factual string) and
citations (an array of document IDs). Cite only the supplied document IDs. If the
documents do not answer the question, say so and return an empty citations array."""


@dataclass(frozen=True)
class RagDocument:
    document_id: str
    title: str
    content: str


RAG_DOCUMENTS: tuple[RagDocument, ...] = (
    RagDocument(
        document_id="policy-refund-window",
        title="Refund window",
        content="A delivered order can be refunded within 30 calendar days of delivery.",
    ),
    RagDocument(
        document_id="policy-cancel-processing",
        title="Cancellation policy",
        content="An order can be cancelled only while its status is processing.",
    ),
    RagDocument(
        document_id="policy-shipping-tracking",
        title="Tracking availability",
        content="Tracking is available after an order is shipped and is sent by email.",
    ),
)


class RagAgentProtocolError(RuntimeError):
    """The provider response or controlled RAG contract is invalid."""


def _case_question(case: DatasetCase) -> str:
    if not isinstance(case.input, dict):
        raise RagAgentProtocolError("Dataset Case input must be an object")
    question = case.input.get("question")
    if not isinstance(question, str) or not question.strip():
        raise RagAgentProtocolError("Dataset Case input.question must be a non-empty string")
    return question.strip()


def _vector(value: Any, *, label: str) -> list[float]:
    if not isinstance(value, list) or not value:
        raise RagAgentProtocolError(f"{label} embedding must be a non-empty vector")
    vector: list[float] = []
    for item in value:
        if not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(item):
            raise RagAgentProtocolError(f"{label} embedding contains a non-finite value")
        vector.append(float(item))
    return vector


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise RagAgentProtocolError("query and document embedding dimensions differ")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise RagAgentProtocolError("embedding vector norm must be non-zero")
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


class LiveRagAgent:
    """A real provider-backed RAG task with local, inspectable vector ranking."""

    def __init__(
        self,
        config: LiveProviderConfig,
        *,
        embedding: LiveEmbeddingConfig,
        documents: tuple[RagDocument, ...] = RAG_DOCUMENTS,
        top_k: int = 2,
        system_prompt: str = RAG_SYSTEM_PROMPT,
    ) -> None:
        if not documents:
            raise ValueError("documents must be non-empty")
        if top_k < 1 or top_k > len(documents):
            raise ValueError("top_k must be between 1 and the document count")
        if len({document.document_id for document in documents}) != len(documents):
            raise ValueError("document IDs must be unique")
        self.config = config
        self.embedding = embedding
        self.documents = documents
        self.top_k = top_k
        self.system_prompt = system_prompt.strip()
        self.tracer = trace.get_tracer("agent-eval-live-rag-agent", "0.1.0")

    def run(self, case: DatasetCase) -> TaskResult:
        question = _case_question(case)
        chat_client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
            max_retries=0,
        )
        embedding_client = OpenAI(
            api_key=self.embedding.api_key,
            base_url=self.embedding.base_url,
            timeout=self.embedding.timeout_seconds,
            max_retries=0,
        )
        try:
            documents, embedding_tokens = self._retrieve(embedding_client, question)
            answer, citations, chat_usage, response_model = self._answer(
                chat_client, question, documents
            )
            usage = {
                "input_tokens": embedding_tokens + chat_usage["input_tokens"],
                "output_tokens": chat_usage["output_tokens"],
                "total_tokens": embedding_tokens + chat_usage["total_tokens"],
                "embedding_input_tokens": embedding_tokens,
            }
            return TaskResult(
                output={
                    "answer": answer,
                    "citations": citations,
                    "retrieved_document_ids": [item["document_id"] for item in documents],
                },
                usage=usage,
                metadata={
                    "execution_origin": "real_llm",
                    "chat_provider_origin": _endpoint_origin(self.config.base_url),
                    "embedding_provider_origin": _endpoint_origin(self.embedding.base_url),
                    "configured_chat_model": self.config.model,
                    "configured_embedding_model": self.embedding.model,
                    "response_models": [response_model],
                },
            )
        except RagAgentProtocolError:
            raise
        except Exception as exc:
            raise RagAgentProtocolError(
                "real RAG Agent provider call failed: " + _safe_error(exc, self.config.api_key)
            ) from exc
        finally:
            chat_client.close()
            embedding_client.close()

    def _retrieve(
        self, client: OpenAI, question: str
    ) -> tuple[list[dict[str, Any]], int]:
        with self.tracer.start_as_current_span("embedding-vector-retrieval") as span:
            span.set_attribute("openinference.span.kind", "RETRIEVER")
            span.set_attribute("gen_ai.operation.name", "embeddings")
            span.set_attribute("gen_ai.request.model", self.embedding.model)
            span.set_attribute(
                "agent_eval.input", json.dumps({"query": question}, ensure_ascii=True)
            )
            try:
                inputs = [question, *[document.content for document in self.documents]]
                response = client.embeddings.create(model=self.embedding.model, input=inputs)
                data = list(getattr(response, "data", []) or [])
                if len(data) != len(inputs):
                    raise RagAgentProtocolError("embedding response count does not match inputs")
                indexed = sorted(data, key=lambda item: int(getattr(item, "index", -1)))
                response_indexes = [int(getattr(item, "index", -1)) for item in indexed]
                if response_indexes != list(range(len(inputs))):
                    raise RagAgentProtocolError("embedding response indexes are incomplete")
                query_vector = _vector(getattr(indexed[0], "embedding", None), label="query")
                ranked = []
                for document, item in zip(self.documents, indexed[1:], strict=True):
                    score = _cosine_similarity(
                        query_vector,
                        _vector(getattr(item, "embedding", None), label=document.document_id),
                    )
                    ranked.append((score, document))
                selected = [
                    {
                        "document_id": document.document_id,
                        "title": document.title,
                        "content": document.content,
                        "similarity": round(score, 8),
                    }
                    for score, document in sorted(
                        ranked, key=lambda item: item[0], reverse=True
                    )[: self.top_k]
                ]
                embedding_tokens = self._embedding_usage(getattr(response, "usage", None))
                span.set_attribute("gen_ai.usage.input_tokens", embedding_tokens)
                # Embeddings consume input only. Persist explicit zero output and
                # matching total so the normalized Span usage is complete.
                span.set_attribute("gen_ai.usage.output_tokens", 0)
                span.set_attribute("gen_ai.usage.total_tokens", embedding_tokens)
                span.set_attribute("agent_eval.usage.present", True)
                span.set_attribute("agent_eval.embedding.model", self.embedding.model)
                span.set_attribute(
                    "retrieval.document_ids", [item["document_id"] for item in selected]
                )
                span.set_attribute(
                    "agent_eval.usage",
                    json.dumps(
                        {
                            "input_tokens": embedding_tokens,
                            "output_tokens": 0,
                            "total_tokens": embedding_tokens,
                        },
                        sort_keys=True,
                    ),
                )
                span.set_attribute(
                    "agent_eval.output",
                    json.dumps(selected, ensure_ascii=True, sort_keys=True),
                )
                return selected, embedding_tokens
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise

    def _answer(
        self, client: OpenAI, question: str, documents: list[dict[str, Any]]
    ) -> tuple[str, list[str], dict[str, int], str]:
        with self.tracer.start_as_current_span("rag-answer-generation") as span:
            span.set_attribute("openinference.span.kind", "LLM")
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.request.model", self.config.model)
            payload = {"question": question, "documents": documents}
            span.set_attribute(
                "agent_eval.input", json.dumps(payload, ensure_ascii=True, sort_keys=True)
            )
            try:
                response = client.chat.completions.create(
                    model=self.config.model,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
                    ],
                )
                if not response.choices:
                    raise RagAgentProtocolError("upstream response contains no choices")
                content = response.choices[0].message.content
                if not isinstance(content, str) or not content.strip():
                    raise RagAgentProtocolError("RAG answer response is empty")
                try:
                    output = json.loads(content)
                except json.JSONDecodeError as exc:
                    raise RagAgentProtocolError("RAG answer is not valid JSON") from exc
                answer, citations = self._validated_answer(output, documents)
                response_model = str(response.model or "").strip()
                request_id = str(response.id or "").strip()
                if not response_model or not request_id:
                    raise RagAgentProtocolError(
                        "upstream response is missing request id or model identity"
                    )
                if self.config.api_key in response_model or self.config.api_key in request_id:
                    raise RagAgentProtocolError(
                        "upstream response placed credential material in public metadata"
                    )
                usage = self._chat_usage(getattr(response, "usage", None))
                span.set_attribute("gen_ai.response.model", response_model)
                span.set_attribute("gen_ai.response.id", request_id)
                span.set_attribute("gen_ai.usage.input_tokens", usage["input_tokens"])
                span.set_attribute("gen_ai.usage.output_tokens", usage["output_tokens"])
                span.set_attribute("gen_ai.usage.total_tokens", usage["total_tokens"])
                span.set_attribute("agent_eval.usage.present", True)
                span.set_attribute("agent_eval.usage", json.dumps(usage, sort_keys=True))
                span.set_attribute(
                    "agent_eval.output",
                    json.dumps({"answer": answer, "citations": citations}, ensure_ascii=True),
                )
                return answer, citations, usage, response_model
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise

    @staticmethod
    def _embedding_usage(usage: Any) -> int:
        value = getattr(usage, "prompt_tokens", None)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise RagAgentProtocolError("embedding response is missing valid usage.prompt_tokens")
        return value

    @staticmethod
    def _chat_usage(usage: Any) -> dict[str, int]:
        values: dict[str, int] = {}
        for source, target in (
            ("prompt_tokens", "input_tokens"),
            ("completion_tokens", "output_tokens"),
            ("total_tokens", "total_tokens"),
        ):
            value = getattr(usage, source, None)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RagAgentProtocolError(f"upstream response is missing valid usage.{source}")
            values[target] = value
        if values["total_tokens"] <= 0 or values["total_tokens"] < (
            values["input_tokens"] + values["output_tokens"]
        ):
            raise RagAgentProtocolError("upstream chat usage totals are inconsistent")
        return values

    @staticmethod
    def _validated_answer(output: Any, documents: list[dict[str, Any]]) -> tuple[str, list[str]]:
        if not isinstance(output, dict):
            raise RagAgentProtocolError("RAG answer JSON must be an object")
        if set(output) != {"answer", "citations"}:
            raise RagAgentProtocolError("RAG answer JSON must contain only answer and citations")
        answer = output.get("answer")
        citations = output.get("citations")
        if not isinstance(answer, str) or not answer.strip():
            raise RagAgentProtocolError("RAG answer must be a non-empty string")
        if not isinstance(citations, list) or not all(isinstance(item, str) for item in citations):
            raise RagAgentProtocolError("RAG citations must be an array of document IDs")
        if len(set(citations)) != len(citations):
            raise RagAgentProtocolError("RAG citations must not contain duplicates")
        available = {str(item["document_id"]) for item in documents}
        if not set(citations).issubset(available):
            raise RagAgentProtocolError("RAG answer cited a document that was not retrieved")
        return answer.strip(), citations
