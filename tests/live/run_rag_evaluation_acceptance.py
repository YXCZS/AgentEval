"""Run the real RAG baseline/candidate evaluation with a platform-managed Judge.

This is an explicit live acceptance lane. It never provides a fake model, answer,
or Judge fallback: missing credentials, an unavailable Worker, or an invalid Judge
response makes the process fail.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from agent_eval import AgentRelease, Client, ExperimentResult, ExperimentRunner
from dotenv import load_dotenv

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.live.provider_preflight import (
    LiveEmbeddingConfig,
    LiveProviderConfig,
    LiveProviderConfigurationError,
    run_embedding_preflight,
    run_preflight,
)
from tests.live.rag_agent import RAG_SYSTEM_PROMPT, LiveRagAgent
from tests.live.rag_dataset import (
    EVALUATOR_DEFINITIONS,
    RagAcceptanceResources,
    ensure_rag_acceptance_resources,
)
from tests.live.run_tool_acceptance import LiveAcceptanceConfigurationError, PlatformConfig

BASELINE_SYSTEM_PROMPT = RAG_SYSTEM_PROMPT
CANDIDATE_SYSTEM_PROMPT = (
    RAG_SYSTEM_PROMPT
    + "\nBefore answering, check that every citation is from the retrieved documents "
    "and that the answer does not add policy details absent from those documents."
)

JUDGE_NAME = "live_rag_answer_groundedness"
JUDGE_VERSION = "1.0.0"
JUDGE_PROMPT_TEMPLATE = """Question: {{ input | json }}
Agent output: {{ actual_output | json }}
Required evaluation criteria: {{ criteria | json }}
Retrieved trajectory evidence: {{ trace | json }}

Evaluate only whether the answer is grounded in its retrieved policy documents,
answers the question, and uses citations consistently. Do not invent policy facts.
Return the requested JSON object."""
JUDGE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["score", "explanation", "evidence"],
    "additionalProperties": False,
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "explanation": {"type": "string", "minLength": 1},
        "evidence": {"type": "array", "items": {"type": "object"}},
        "label": {"type": "string"},
        "passed": {"type": "boolean"},
    },
}


class LiveRagEvaluationError(RuntimeError):
    """The real RAG evaluation lane could not produce verified evidence."""


@dataclass(frozen=True)
class LiveRagEvaluationResult:
    accepted: bool
    completed_at: str
    providers: dict[str, Any]
    dataset: dict[str, Any]
    judge: dict[str, Any]
    releases: dict[str, Any]
    experiments: dict[str, Any]
    comparison: dict[str, Any]
    gate: dict[str, Any]
    verified_resources: dict[str, Any]


def _safe_error(exc: Exception, secrets: tuple[str, ...]) -> str:
    message = str(exc)
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return re.sub(r"\b(?:sk|aek)_[A-Za-z0-9_-]{8,}\b", "[REDACTED]", message)[:1000]


def _request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    try:
        response = client.request(method, path, json=payload)
    except httpx.HTTPError as exc:
        raise LiveRagEvaluationError(f"platform {method} {path} failed") from exc
    if response.is_error:
        raise LiveRagEvaluationError(
            f"platform rejected {method} {path} with HTTP {response.status_code}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise LiveRagEvaluationError(
            f"platform returned invalid JSON for {method} {path}"
        ) from exc
    if not isinstance(body, (dict, list)):
        raise LiveRagEvaluationError(
            f"platform returned an invalid JSON shape for {method} {path}"
        )
    return body


def _object_response(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = _request_json(client, method, path, payload=payload)
    if not isinstance(body, dict):
        raise LiveRagEvaluationError(f"platform returned a non-object response for {path}")
    return body


def _connection_name(provider: LiveProviderConfig) -> str:
    parsed = urlsplit(provider.base_url)
    fingerprint = hashlib.sha256(
        f"{provider.base_url.rstrip('/')}|{provider.model}".encode()
    ).hexdigest()[:12]
    return f"live-rag-judge-{parsed.hostname or 'provider'}-{fingerprint}"


def _ensure_provider_connection(
    client: httpx.Client,
    *,
    project_id: str,
    provider: LiveProviderConfig,
) -> dict[str, Any]:
    """Create once, then reuse the encrypted connection by immutable identity."""

    name = _connection_name(provider)
    connections = _request_json(
        client, "GET", f"/projects/{project_id}/provider-connections"
    )
    assert isinstance(connections, list)
    matches = [item for item in connections if item.get("name") == name]
    if len(matches) > 1:
        raise LiveRagEvaluationError(f"multiple managed Judge connections are named {name!r}")
    if matches:
        existing = matches[0]
        if (
            str(existing.get("base_url", "")).rstrip("/") != provider.base_url.rstrip("/")
            or existing.get("model") != provider.model
            or existing.get("provider") != "openai_compatible"
            or not existing.get("enabled")
            or existing.get("status") != "active"
        ):
            raise LiveRagEvaluationError(
                "existing managed Judge connection configuration does not match "
                "the live chat provider"
            )
        return existing

    # The test endpoint proves the requested provider before an encrypted record is
    # saved. The create endpoint validates again immediately before persistence.
    _object_response(
        client,
        "POST",
        f"/projects/{project_id}/provider-connections/test",
        payload={
            "provider": "openai_compatible",
            "base_url": provider.base_url,
            "model": provider.model,
            "api_key": provider.api_key,
            "default_parameters": {},
        },
    )
    return _object_response(
        client,
        "POST",
        f"/projects/{project_id}/provider-connections",
        payload={
            "name": name,
            "provider": "openai_compatible",
            "base_url": provider.base_url,
            "model": provider.model,
            "api_key": provider.api_key,
            "default_parameters": {},
        },
    )


def _judge_definition(connection_id: str, model: str) -> dict[str, Any]:
    return {
        "name": JUDGE_NAME,
        "version": JUDGE_VERSION,
        "evaluator_type": "llm_judge",
        "requires": ["output", "trace"],
        "supported_agent_types": ["rag"],
        "score_min": 0,
        "score_max": 1,
        "direction": "higher_is_better",
        "default_threshold": 0.8,
        "rubric": (
            "Score factual grounding, retrieval relevance, and citation consistency "
            "from 0 to 1."
        ),
        "provider_connection_id": connection_id,
        "judge_model": model,
        "prompt_template": JUDGE_PROMPT_TEMPLATE,
        "output_schema": JUDGE_OUTPUT_SCHEMA,
        "sampling_parameters": {
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 500,
            "seed": None,
        },
        "config": {"timeout_seconds": 60, "max_retries": 2, "retry_backoff_seconds": 1},
    }


def _ensure_judge_evaluator(
    client: httpx.Client,
    *,
    project_id: str,
    provider_connection_id: str,
    model: str,
) -> dict[str, Any]:
    definition = _judge_definition(provider_connection_id, model)
    evaluators = _request_json(client, "GET", f"/projects/{project_id}/evaluators")
    assert isinstance(evaluators, list)
    matches = [
        item
        for item in evaluators
        if item.get("name") == JUDGE_NAME and item.get("version") == JUDGE_VERSION
    ]
    if len(matches) > 1:
        raise LiveRagEvaluationError("multiple live RAG Judge evaluator versions exist")
    if matches:
        existing = matches[0]
        identity_fields = (
            "evaluator_type",
            "requires",
            "supported_agent_types",
            "score_min",
            "score_max",
            "direction",
            "default_threshold",
            "rubric",
            "provider_connection_id",
            "judge_model",
            "prompt_template",
            "output_schema",
            "sampling_parameters",
            "config",
        )
        if any(existing.get(field) != definition[field] for field in identity_fields):
            raise LiveRagEvaluationError(
                "existing live RAG Judge evaluator has configuration drift; "
                "immutable evaluator versions cannot be edited"
            )
        if not existing.get("enabled"):
            raise LiveRagEvaluationError("existing live RAG Judge evaluator is disabled")
        return existing
    return _object_response(
        client,
        "POST",
        f"/projects/{project_id}/evaluators",
        payload=definition,
    )


def _register_release(
    client: Client,
    *,
    label: str,
    variant: str,
    system_prompt: str,
    chat: LiveProviderConfig,
    embedding: LiveEmbeddingConfig,
) -> AgentRelease:
    prompt_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
    identity = hashlib.sha256(
        json.dumps(
            {
                "variant": variant,
                "chat_model": chat.model,
                "embedding_model": embedding.model,
                "prompt_sha256": prompt_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return client.register_release(
        label=label,
        agent_type="rag",
        release_identity=f"live-rag-sha256:{identity}",
        source_revision=f"prompt-sha256:{prompt_hash}",
        metadata={
            "acceptance_kind": "real_rag_agent_judge",
            "variant": variant,
            "configured_chat_model": chat.model,
            "configured_embedding_model": embedding.model,
            "prompt_sha256": prompt_hash,
        },
    )


def _run_release(
    client: Client,
    *,
    resources: RagAcceptanceResources,
    evaluator_version_ids: list[str],
    release: AgentRelease,
    chat: LiveProviderConfig,
    embedding: LiveEmbeddingConfig,
    system_prompt: str,
    name: str,
    baseline_experiment_id: str | None = None,
) -> ExperimentResult:
    dataset = client.get_dataset(resources.dataset_id, version_id=resources.dataset_version_id)
    return ExperimentRunner(client).run(
        dataset=dataset,
        task=LiveRagAgent(
            chat,
            embedding=embedding,
            system_prompt=system_prompt,
        ).run,
        release=release,
        evaluator_version_ids=evaluator_version_ids,
        name=name,
        evidence_policy="rag_trajectory_required",
        baseline_experiment_id=baseline_experiment_id,
        max_concurrency=1,
        timeout_seconds=max(60.0, chat.timeout_seconds * 3, embedding.timeout_seconds * 3),
    )


def _wait_for_judge(
    client: httpx.Client,
    *,
    project_id: str,
    experiment_id: str,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = _object_response(
            client, "GET", f"/projects/{project_id}/experiments/{experiment_id}"
        )
        if last.get("status") in {"completed", "partial", "failed", "cancelled"}:
            return last
        time.sleep(1)
    raise LiveRagEvaluationError(
        "managed Judge did not reach a terminal Experiment state within "
        f"{int(timeout_seconds)} seconds"
    )


def _judge_scores(report: dict[str, Any]) -> list[dict[str, Any]]:
    scores: list[dict[str, Any]] = []
    for case in report.get("cases", []):
        if not isinstance(case, dict):
            continue
        for score in case.get("scores", []):
            if isinstance(score, dict) and score.get("metric_name") == JUDGE_NAME:
                scores.append(score)
    if not scores:
        raise LiveRagEvaluationError("managed Judge did not persist any RAG scores")
    if any(score.get("status") not in {"passed", "failed"} for score in scores):
        raise LiveRagEvaluationError("managed Judge has missing or error score evidence")
    if any(
        not isinstance(score.get("provenance"), dict)
        or score["provenance"].get("source") != "platform_provider"
        for score in scores
    ):
        raise LiveRagEvaluationError("RAG Judge scores do not prove platform Provider provenance")
    return scores


def run_rag_evaluation_acceptance(
    platform: PlatformConfig,
    chat: LiveProviderConfig,
    embedding: LiveEmbeddingConfig,
) -> LiveRagEvaluationResult:
    # No platform data is created until both real Agent providers have succeeded.
    chat_evidence = run_preflight(chat)
    embedding_evidence = run_embedding_preflight(embedding)
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
        connection = _ensure_provider_connection(
            platform_http, project_id=platform.project_id, provider=chat
        )
        judge = _ensure_judge_evaluator(
            platform_http,
            project_id=platform.project_id,
            provider_connection_id=str(connection["id"]),
            model=chat.model,
        )
        evaluator_ids = [*resources.evaluator_version_ids, str(judge["id"])]
        baseline_release = _register_release(
            sdk,
            label=f"live-rag-baseline-{timestamp}",
            variant="baseline",
            system_prompt=BASELINE_SYSTEM_PROMPT,
            chat=chat,
            embedding=embedding,
        )
        candidate_release = _register_release(
            sdk,
            label=f"live-rag-candidate-{timestamp}",
            variant="candidate",
            system_prompt=CANDIDATE_SYSTEM_PROMPT,
            chat=chat,
            embedding=embedding,
        )
        baseline = _run_release(
            sdk,
            resources=resources,
            evaluator_version_ids=evaluator_ids,
            release=baseline_release,
            chat=chat,
            embedding=embedding,
            system_prompt=BASELINE_SYSTEM_PROMPT,
            name=f"Live RAG baseline {timestamp}",
        )
        candidate = _run_release(
            sdk,
            resources=resources,
            evaluator_version_ids=evaluator_ids,
            release=candidate_release,
            chat=chat,
            embedding=embedding,
            system_prompt=CANDIDATE_SYSTEM_PROMPT,
            name=f"Live RAG candidate {timestamp}",
            baseline_experiment_id=baseline.experiment.id,
        )
        baseline_detail = _wait_for_judge(
            platform_http,
            project_id=platform.project_id,
            experiment_id=baseline.experiment.id,
        )
        candidate_detail = _wait_for_judge(
            platform_http,
            project_id=platform.project_id,
            experiment_id=candidate.experiment.id,
        )
        baseline_report = _object_response(
            platform_http,
            "GET",
            f"/projects/{platform.project_id}/reports/{baseline.experiment.id}",
        )
        candidate_report = _object_response(
            platform_http,
            "GET",
            f"/projects/{platform.project_id}/reports/{candidate.experiment.id}",
        )
        baseline_judge_scores = _judge_scores(baseline_report)
        candidate_judge_scores = _judge_scores(candidate_report)
        comparison = _object_response(
            platform_http,
            "POST",
            f"/projects/{platform.project_id}/comparisons",
            payload={"run_ids": [baseline.experiment.id, candidate.experiment.id]},
        )
        gate_rules = [
            {
                "metric_name": definition["name"],
                "minimum": 1,
                "require_all_passed": True,
            }
            for definition in EVALUATOR_DEFINITIONS
        ] + [
            {
                "metric_name": JUDGE_NAME,
                "minimum": 0.8,
                "require_all_passed": True,
            }
        ]
        gate = _object_response(
            platform_http,
            "POST",
            f"/projects/{platform.project_id}/runs/{candidate.experiment.id}/regression-gate",
            payload={"rules": gate_rules},
        )
        all_trace_ids = sorted(
            {
                item.trace_id
                for item in [*baseline.items, *candidate.items]
                if item.trace_id
            }
        )
        resource_paths = [
            f"/projects/{platform.project_id}/datasets/{resources.dataset_id}/versions/"
            f"{resources.dataset_version_id}",
            f"/projects/{platform.project_id}/experiments/{baseline.experiment.id}",
            f"/projects/{platform.project_id}/experiments/{candidate.experiment.id}",
            f"/projects/{platform.project_id}/reports/{baseline.experiment.id}",
            f"/projects/{platform.project_id}/reports/{candidate.experiment.id}",
            *[
                f"/projects/{platform.project_id}/traces/{trace_id}"
                for trace_id in all_trace_ids
            ],
        ]
        for path in resource_paths:
            _object_response(platform_http, "GET", path)

    if (
        baseline.experiment.dataset_version_id != resources.dataset_version_id
        or candidate.experiment.dataset_version_id != resources.dataset_version_id
    ):
        raise LiveRagEvaluationError("baseline and candidate did not use the same Dataset Version")
    if candidate.experiment.baseline_run_id != baseline.experiment.id:
        raise LiveRagEvaluationError("candidate is not linked to the persisted RAG baseline")
    if comparison.get("baseline_run_id") != baseline.experiment.id:
        raise LiveRagEvaluationError("comparison did not use the persisted RAG baseline")
    if gate.get("run_id") != candidate.experiment.id:
        raise LiveRagEvaluationError("Gate did not evaluate the persisted RAG candidate")
    if (
        baseline_detail.get("status") != "completed"
        or candidate_detail.get("status") != "completed"
    ):
        raise LiveRagEvaluationError("RAG experiments did not complete after managed Judge scoring")

    return LiveRagEvaluationResult(
        accepted=True,
        completed_at=datetime.now(UTC).isoformat(),
        providers={
            "chat": {
                "endpoint_origin": chat_evidence.endpoint_origin,
                "configured_model": chat_evidence.configured_model,
                "response_model": chat_evidence.response_model,
                "usage_present": chat_evidence.total_tokens > 0,
            },
            "embedding": {
                "endpoint_origin": embedding_evidence.endpoint_origin,
                "configured_model": embedding_evidence.configured_model,
                "response_model": embedding_evidence.response_model,
                "vector_dimensions": embedding_evidence.vector_dimensions,
                "usage_present": embedding_evidence.input_tokens > 0,
            },
        },
        dataset={
            "id": resources.dataset_id,
            "version_id": resources.dataset_version_id,
            "manifest_sha256": resources.manifest_sha256,
        },
        judge={
            "provider_connection_id": connection["id"],
            "credential_mask": connection["credential_mask"],
            "evaluator_id": judge["id"],
            "evaluator_version": JUDGE_VERSION,
            "model": chat.model,
            "baseline_score_statuses": [score["status"] for score in baseline_judge_scores],
            "candidate_score_statuses": [score["status"] for score in candidate_judge_scores],
        },
        releases={"baseline_id": baseline_release.id, "candidate_id": candidate_release.id},
        experiments={
            "baseline_id": baseline.experiment.id,
            "baseline_status": baseline_detail["status"],
            "candidate_id": candidate.experiment.id,
            "candidate_status": candidate_detail["status"],
        },
        comparison={
            "baseline_run_id": comparison.get("baseline_run_id"),
            "new_failure_count": len(comparison.get("new_failures", [])),
            "recovered_case_count": len(comparison.get("recovered_cases", [])),
            "missing_evidence_count": len(comparison.get("missing_evidence", [])),
            "metrics": comparison.get("metric_comparisons", []),
        },
        gate=gate,
        verified_resources={
            "api_paths": resource_paths,
            "trace_ids": all_trace_ids,
            "workbench_links": {
                "Baseline Experiment": f"/?view=runs&run_id={baseline.experiment.id}",
                "Candidate Experiment": f"/?view=runs&run_id={candidate.experiment.id}",
                **{
                    f"Trace {trace_id}": f"/?view=traces&trace_id={trace_id}"
                    for trace_id in all_trace_ids
                },
            },
        },
    )


def main() -> int:
    load_dotenv(override=False)
    try:
        result = run_rag_evaluation_acceptance(
            PlatformConfig.from_environment(),
            LiveProviderConfig.from_environment(),
            LiveEmbeddingConfig.from_environment(),
        )
    except (LiveAcceptanceConfigurationError, LiveProviderConfigurationError) as exc:
        print(f"live RAG evaluation configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            "live RAG evaluation failed: "
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
