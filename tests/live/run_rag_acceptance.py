"""Run the real RAG ingestion lane and verify persisted retrieval evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from agent_eval import Client, ExperimentResult, ExperimentRunner
from dotenv import load_dotenv

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.live.provider_preflight import (
    LiveEmbeddingConfig,
    LiveEmbeddingEvidence,
    LiveProviderConfig,
    LiveProviderConfigurationError,
    LiveProviderEvidence,
    run_embedding_preflight,
    run_preflight,
)
from tests.live.rag_agent import RAG_SYSTEM_PROMPT, LiveRagAgent
from tests.live.rag_dataset import RagAcceptanceResources, ensure_rag_acceptance_resources
from tests.live.run_tool_acceptance import LiveAcceptanceConfigurationError, PlatformConfig


class LiveRagAcceptanceError(RuntimeError):
    """The real RAG ingestion lane could not produce verifiable persisted evidence."""


@dataclass(frozen=True)
class LiveRagAcceptanceResult:
    accepted: bool
    completed_at: str
    chat_provider: dict[str, Any]
    embedding_provider: dict[str, Any]
    dataset: dict[str, Any]
    release: dict[str, Any]
    experiment: dict[str, Any]
    verified_resources: dict[str, Any]


def _safe_error(exc: Exception, secrets: tuple[str, ...]) -> str:
    message = str(exc)
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[REDACTED]")
    message = re.sub(r"\b(?:sk|aek)_[A-Za-z0-9_-]{8,}\b", "[REDACTED]", message)
    return message[:1000]


def _release_identity(
    chat: LiveProviderConfig, embedding: LiveEmbeddingConfig
) -> tuple[str, str]:
    prompt_hash = hashlib.sha256(RAG_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    identity = hashlib.sha256(
        json.dumps(
            {
                "chat_model": chat.model,
                "embedding_model": embedding.model,
                "prompt_sha256": prompt_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return identity, prompt_hash


def _get_json(client: httpx.Client, path: str) -> dict[str, Any]:
    try:
        response = client.get(path)
    except httpx.HTTPError as exc:
        raise LiveRagAcceptanceError(f"platform GET {path} failed") from exc
    if response.is_error:
        raise LiveRagAcceptanceError(
            f"platform rejected GET {path} with HTTP {response.status_code}"
        )
    body = response.json()
    if not isinstance(body, dict):
        raise LiveRagAcceptanceError(f"platform returned a non-object response for GET {path}")
    return body


def _has_usage(span: dict[str, Any], *, require_output: bool) -> bool:
    usage = span.get("usage")
    if not isinstance(usage, dict):
        return False
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    total_tokens = usage.get("total_tokens")
    if not isinstance(input_tokens, int) or input_tokens <= 0:
        return False
    if require_output and (not isinstance(output_tokens, int) or output_tokens < 0):
        return False
    if not isinstance(total_tokens, int) or total_tokens < input_tokens:
        return False
    return not require_output or total_tokens >= input_tokens + output_tokens


def _verify_persisted_rag_trace(trace: dict[str, Any]) -> dict[str, Any]:
    spans = trace.get("spans")
    if not isinstance(spans, list):
        raise LiveRagAcceptanceError("persisted Trace has no Span list")
    retrieval_spans = [
        span for span in spans if isinstance(span, dict) and span.get("kind") == "retrieval"
    ]
    llm_spans = [span for span in spans if isinstance(span, dict) and span.get("kind") == "llm"]
    if not retrieval_spans:
        raise LiveRagAcceptanceError("persisted Trace is missing a RETRIEVER Span")
    if not llm_spans:
        raise LiveRagAcceptanceError("persisted Trace is missing an LLM Span")
    retrieval = retrieval_spans[0]
    attributes = retrieval.get("attributes")
    document_ids = (
        attributes.get("retrieval.document_ids") if isinstance(attributes, dict) else None
    )
    if (
        not isinstance(document_ids, list)
        or not document_ids
        or not all(isinstance(item, str) for item in document_ids)
    ):
        raise LiveRagAcceptanceError("persisted RETRIEVER Span is missing document IDs")
    if not _has_usage(retrieval, require_output=False):
        raise LiveRagAcceptanceError("persisted RETRIEVER Span is missing embedding usage")
    if not any(_has_usage(span, require_output=True) for span in llm_spans):
        raise LiveRagAcceptanceError("persisted LLM Span is missing chat usage")
    return {
        "trace_id": trace.get("trace_id"),
        "retrieved_document_ids": document_ids,
        "llm_span_count": len(llm_spans),
    }


def _run_experiment(
    client: Client,
    *,
    resources: RagAcceptanceResources,
    chat: LiveProviderConfig,
    embedding: LiveEmbeddingConfig,
    timestamp: str,
) -> ExperimentResult:
    identity, prompt_hash = _release_identity(chat, embedding)
    release = client.register_release(
        label=f"live-rag-ingestion-{timestamp}",
        agent_type="rag",
        release_identity=f"live-rag-sha256:{identity}",
        source_revision=f"prompt-sha256:{prompt_hash}",
        metadata={
            "acceptance_kind": "real_rag_agent",
            "configured_chat_model": chat.model,
            "configured_embedding_model": embedding.model,
            "prompt_sha256": prompt_hash,
        },
    )
    dataset = client.get_dataset(resources.dataset_id, version_id=resources.dataset_version_id)
    result = ExperimentRunner(client).run(
        dataset=dataset,
        task=LiveRagAgent(chat, embedding=embedding).run,
        release=release,
        evaluator_version_ids=list(resources.evaluator_version_ids),
        name=f"Live RAG ingestion {timestamp}",
        evidence_policy="rag_trajectory_required",
        max_concurrency=1,
        timeout_seconds=max(45.0, chat.timeout_seconds * 3, embedding.timeout_seconds * 3),
    )
    return result


def run_rag_acceptance(
    platform: PlatformConfig,
    chat: LiveProviderConfig,
    embedding: LiveEmbeddingConfig,
) -> LiveRagAcceptanceResult:
    # Both upstream dependencies must succeed before Dataset, Release, or Experiment creation.
    chat_evidence: LiveProviderEvidence = run_preflight(chat)
    embedding_evidence: LiveEmbeddingEvidence = run_embedding_preflight(embedding)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    with httpx.Client(
        base_url=platform.base_url,
        headers={"X-Project-Key": platform.api_key},
        timeout=platform.timeout_seconds,
    ) as platform_http, Client(
        base_url=platform.base_url,
        project_id=platform.project_id,
        api_key=platform.api_key,
        timeout=platform.timeout_seconds,
    ) as sdk:
        resources = ensure_rag_acceptance_resources(platform_http, platform.project_id)
        result = _run_experiment(
            sdk,
            resources=resources,
            chat=chat,
            embedding=embedding,
            timestamp=timestamp,
        )
        trace_ids = [item.trace_id for item in result.items if item.trace_id]
        if len(trace_ids) != len(result.items) or len(set(trace_ids)) != len(trace_ids):
            raise LiveRagAcceptanceError(
                "every RAG acceptance Item must reference one distinct persisted Trace"
            )
        trace_paths = [
            f"/projects/{platform.project_id}/traces/{trace_id}" for trace_id in trace_ids
        ]
        trace_checks = [
            _verify_persisted_rag_trace(_get_json(platform_http, path)) for path in trace_paths
        ]
        dataset_path = (
            f"/projects/{platform.project_id}/datasets/{resources.dataset_id}/versions/"
            f"{resources.dataset_version_id}"
        )
        experiment_path = f"/projects/{platform.project_id}/experiments/{result.experiment.id}"
        _get_json(platform_http, dataset_path)
        _get_json(platform_http, experiment_path)

    evidence_complete = all(
        item.status == "completed" and item.evidence_status == "complete"
        for item in result.items
    )
    return LiveRagAcceptanceResult(
        accepted=result.experiment.status == "completed" and evidence_complete,
        completed_at=datetime.now(UTC).isoformat(),
        chat_provider={
            "endpoint_origin": chat_evidence.endpoint_origin,
            "configured_model": chat_evidence.configured_model,
            "response_model": chat_evidence.response_model,
            "usage_present": chat_evidence.total_tokens > 0,
        },
        embedding_provider={
            "endpoint_origin": embedding_evidence.endpoint_origin,
            "configured_model": embedding_evidence.configured_model,
            "response_model": embedding_evidence.response_model,
            "vector_dimensions": embedding_evidence.vector_dimensions,
            "usage_present": embedding_evidence.input_tokens > 0,
        },
        dataset={
            "id": resources.dataset_id,
            "version_id": resources.dataset_version_id,
            "manifest_sha256": resources.manifest_sha256,
        },
        release={"id": result.experiment.agent_version_id},
        experiment={
            "id": result.experiment.id,
            "status": result.experiment.status,
            "evidence_complete": evidence_complete,
        },
        verified_resources={
            "api_paths": [dataset_path, experiment_path, *trace_paths],
            "trace_checks": trace_checks,
            "workbench_links": {
                "RAG Experiment": f"/?view=runs&run_id={result.experiment.id}",
                **{
                    f"Trace {trace_id}": f"/?view=traces&trace_id={trace_id}"
                    for trace_id in trace_ids
                },
            },
        },
    )


def main() -> int:
    load_dotenv(override=False)
    try:
        result = run_rag_acceptance(
            PlatformConfig.from_environment(),
            LiveProviderConfig.from_environment(),
            LiveEmbeddingConfig.from_environment(),
        )
    except (LiveAcceptanceConfigurationError, LiveProviderConfigurationError) as exc:
        print(f"live RAG acceptance configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            "live RAG acceptance failed: "
            + _safe_error(
                exc,
                (
                    os.getenv("AGENT_EVAL_API_KEY", ""),
                    os.getenv("LIVE_ACCEPTANCE_API_KEY", ""),
                    os.getenv("LIVE_ACCEPTANCE_EMBEDDING_API_KEY", ""),
                    os.getenv("EMBEDDING_API_KEY", ""),
                ),
            ),
            file=sys.stderr,
        )
        return 3
    print(json.dumps(asdict(result), ensure_ascii=True, indent=2))
    return 0 if result.accepted else 4


if __name__ == "__main__":
    raise SystemExit(main())
