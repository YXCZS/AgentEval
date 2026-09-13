"""Run the real SDK Tool Agent baseline/candidate acceptance lane."""

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
from urllib.parse import urlsplit

import httpx
from agent_eval import AgentRelease, Client, ExperimentResult, ExperimentRunner
from dotenv import load_dotenv

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.live.provider_preflight import (
    LiveProviderConfig,
    LiveProviderConfigurationError,
    LiveProviderEvidence,
    run_preflight,
)
from tests.live.tool_agent import SYSTEM_PROMPT, LiveToolAgent
from tests.live.tool_dataset import (
    EVALUATOR_DEFINITIONS,
    ToolAcceptanceResources,
    ensure_tool_acceptance_resources,
)

BASELINE_SYSTEM_PROMPT = SYSTEM_PROMPT
CANDIDATE_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + "\nChoose the most specific read-only tool for the customer's stated intent, "
    "and ground the final answer only in the returned tool result."
)


class LiveAcceptanceConfigurationError(ValueError):
    """The platform-side live acceptance environment is incomplete."""


class LiveAcceptanceError(RuntimeError):
    """The real acceptance lane could not complete its platform workflow."""


@dataclass(frozen=True)
class PlatformConfig:
    base_url: str
    project_id: str
    api_key: str
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> PlatformConfig:
        base_url = os.getenv("AGENT_EVAL_BASE_URL", "").strip().rstrip("/")
        project_id = os.getenv("AGENT_EVAL_PROJECT_ID", "").strip()
        api_key = os.getenv("AGENT_EVAL_API_KEY", "").strip()
        missing = [
            name
            for name, value in (
                ("AGENT_EVAL_BASE_URL", base_url),
                ("AGENT_EVAL_PROJECT_ID", project_id),
                ("AGENT_EVAL_API_KEY", api_key),
            )
            if not value
        ]
        if missing:
            raise LiveAcceptanceConfigurationError(
                "missing required platform variables: " + ", ".join(missing)
            )
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise LiveAcceptanceConfigurationError(
                "AGENT_EVAL_BASE_URL must be an absolute HTTP(S) URL"
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise LiveAcceptanceConfigurationError(
                "AGENT_EVAL_BASE_URL must not contain credentials, query, or fragment"
            )
        try:
            _ = parsed.port
        except ValueError as exc:
            raise LiveAcceptanceConfigurationError(
                "AGENT_EVAL_BASE_URL contains an invalid port"
            ) from exc
        return cls(base_url=base_url, project_id=project_id, api_key=api_key)


@dataclass(frozen=True)
class LiveToolAcceptanceResult:
    accepted: bool
    completed_at: str
    provider: dict[str, Any]
    dataset: dict[str, Any]
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
    message = re.sub(r"\b(?:sk|aek)_[A-Za-z0-9_-]{8,}\b", "[REDACTED]", message)
    return message[:1000]


def _prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _register_release(
    client: Client,
    *,
    label: str,
    variant: str,
    prompt: str,
    provider: LiveProviderConfig,
) -> AgentRelease:
    prompt_hash = _prompt_sha256(prompt)
    identity_payload = json.dumps(
        {
            "variant": variant,
            "model": provider.model,
            "prompt_sha256": prompt_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    identity = hashlib.sha256(identity_payload.encode("utf-8")).hexdigest()
    return client.register_release(
        label=label,
        agent_type="tool",
        release_identity=f"live-tool-sha256:{identity}",
        source_revision=f"prompt-sha256:{prompt_hash}",
        metadata={
            "acceptance_kind": "real_tool_agent",
            "variant": variant,
            "configured_model": provider.model,
            "prompt_sha256": prompt_hash,
        },
    )


def _post_json(
    client: httpx.Client,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(path, json=payload)
    if response.is_error:
        raise LiveAcceptanceError(
            f"platform rejected POST {path} with HTTP {response.status_code}"
        )
    body = response.json()
    if not isinstance(body, dict):
        raise LiveAcceptanceError(
            f"platform returned a non-object response for POST {path}"
        )
    return body


def _get_json(client: httpx.Client, path: str) -> dict[str, Any]:
    try:
        response = client.get(path)
    except httpx.HTTPError as exc:
        raise LiveAcceptanceError(f"platform GET {path} failed") from exc
    if response.is_error:
        raise LiveAcceptanceError(
            f"platform rejected GET {path} with HTTP {response.status_code}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise LiveAcceptanceError(
            f"platform returned invalid JSON for GET {path}"
        ) from exc
    if not isinstance(body, dict):
        raise LiveAcceptanceError(
            f"platform returned a non-object response for GET {path}"
        )
    return body


def write_acceptance_artifacts(
    result: LiveToolAcceptanceResult,
    directory: Path = Path("artifacts/live"),
) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.fromisoformat(result.completed_at).strftime("%Y%m%dT%H%M%SZ")
    stem = f"tool-acceptance-{timestamp}"
    json_path = directory / f"{stem}.json"
    markdown_path = directory / f"{stem}.md"
    payload = asdict(result)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    metrics = result.comparison.get("metrics", [])
    metric_lines = [
        f"- `{metric.get('metric_name', 'unknown')}`: comparable="
        f"`{metric.get('comparable', False)}`"
        for metric in metrics
        if isinstance(metric, dict)
    ] or ["- No comparison metrics returned."]
    workbench_lines = [
        f"- [{label}]({path})"
        for label, path in result.verified_resources.get("workbench_links", {}).items()
    ] or ["- No workbench links returned."]
    markdown = "\n".join(
        [
            "# SDK Tool Agent MVP Acceptance",
            "",
            f"- Completed at: `{result.completed_at}`",
            f"- Accepted: `{result.accepted}`",
            f"- Provider origin: `{result.provider.get('endpoint_origin')}`",
            f"- Configured model: `{result.provider.get('configured_model')}`",
            f"- Provider response model: `{result.provider.get('response_model')}`",
            f"- Dataset Version: `{result.dataset.get('version_id')}`",
            f"- Baseline Release: `{result.releases.get('baseline_id')}`",
            f"- Candidate Release: `{result.releases.get('candidate_id')}`",
            f"- Baseline Experiment: `{result.experiments.get('baseline_id')}`",
            f"- Candidate Experiment: `{result.experiments.get('candidate_id')}`",
            f"- Evidence complete: `{result.experiments.get('evidence_complete')}`",
            f"- Gate status: `{result.gate.get('status')}`",
            "",
            "## Comparison Metrics",
            "",
            *metric_lines,
            "",
            "## Verified API Resources",
            "",
            *[
                f"- `{path}`"
                for path in result.verified_resources.get("api_paths", [])
            ],
            "",
            "## Workbench Links",
            "",
            *workbench_lines,
            "",
            "This artifact contains no API keys, authorization headers, prompts, "
            "or raw provider responses.",
            "",
        ]
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def _run_release(
    client: Client,
    *,
    resources: ToolAcceptanceResources,
    release: AgentRelease,
    provider: LiveProviderConfig,
    prompt: str,
    name: str,
    baseline_experiment_id: str | None = None,
) -> ExperimentResult:
    dataset = client.get_dataset(
        resources.dataset_id,
        version_id=resources.dataset_version_id,
    )
    return ExperimentRunner(client).run(
        dataset=dataset,
        task=LiveToolAgent(provider, system_prompt=prompt).run,
        release=release,
        evaluator_version_ids=list(resources.evaluator_version_ids),
        name=name,
        evidence_policy="tool_trajectory_required",
        baseline_experiment_id=baseline_experiment_id,
        max_concurrency=1,
        timeout_seconds=max(30.0, provider.timeout_seconds * 3),
    )


def run_tool_acceptance(
    platform: PlatformConfig,
    provider: LiveProviderConfig,
) -> LiveToolAcceptanceResult:
    # This must remain the first network operation so invalid provider config
    # cannot create Dataset, Evaluator, Release, or Experiment records.
    preflight: LiveProviderEvidence = run_preflight(provider)
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
        resources = ensure_tool_acceptance_resources(platform_http, platform.project_id)
        baseline_release = _register_release(
            sdk,
            label=f"live-tool-baseline-{timestamp}",
            variant="baseline",
            prompt=BASELINE_SYSTEM_PROMPT,
            provider=provider,
        )
        candidate_release = _register_release(
            sdk,
            label=f"live-tool-candidate-{timestamp}",
            variant="candidate",
            prompt=CANDIDATE_SYSTEM_PROMPT,
            provider=provider,
        )
        baseline = _run_release(
            sdk,
            resources=resources,
            release=baseline_release,
            provider=provider,
            prompt=BASELINE_SYSTEM_PROMPT,
            name=f"Live Tool baseline {timestamp}",
        )
        candidate = _run_release(
            sdk,
            resources=resources,
            release=candidate_release,
            provider=provider,
            prompt=CANDIDATE_SYSTEM_PROMPT,
            name=f"Live Tool candidate {timestamp}",
            baseline_experiment_id=baseline.experiment.id,
        )
        comparison = _post_json(
            platform_http,
            f"/projects/{platform.project_id}/comparisons",
            {"run_ids": [baseline.experiment.id, candidate.experiment.id]},
        )
        gate = _post_json(
            platform_http,
            f"/projects/{platform.project_id}/runs/"
            f"{candidate.experiment.id}/regression-gate",
            {
                "rules": [
                    {
                        "metric_name": definition["name"],
                        "minimum": 1,
                        "require_all_passed": True,
                    }
                    for definition in EVALUATOR_DEFINITIONS
                ]
            },
        )

        all_items = [*baseline.items, *candidate.items]
        trace_ids = sorted(
            {
                item.trace_id
                for item in all_items
                if item.trace_id
            }
        )
        if len(trace_ids) != len(all_items):
            raise LiveAcceptanceError(
                "every acceptance Item must reference one distinct persisted Trace"
            )
        resource_paths = [
            f"/projects/{platform.project_id}/datasets/{resources.dataset_id}/versions/"
            f"{resources.dataset_version_id}",
            f"/projects/{platform.project_id}/experiments/{baseline.experiment.id}",
            f"/projects/{platform.project_id}/experiments/{candidate.experiment.id}",
            *[
                f"/projects/{platform.project_id}/traces/{trace_id}"
                for trace_id in trace_ids
            ],
        ]
        for path in resource_paths:
            _get_json(platform_http, path)

    if baseline.experiment.dataset_version_id != resources.dataset_version_id:
        raise LiveAcceptanceError("baseline did not use the prepared Dataset Version")
    if candidate.experiment.dataset_version_id != resources.dataset_version_id:
        raise LiveAcceptanceError("candidate did not use the prepared Dataset Version")
    if candidate.experiment.baseline_run_id != baseline.experiment.id:
        raise LiveAcceptanceError("candidate is not linked to the persisted baseline")
    if comparison.get("baseline_run_id") != baseline.experiment.id:
        raise LiveAcceptanceError("comparison did not use the persisted baseline")
    if gate.get("run_id") != candidate.experiment.id:
        raise LiveAcceptanceError("Gate did not evaluate the persisted candidate")

    item_evidence_complete = all(
        item.status == "completed" and item.evidence_status == "complete"
        for item in all_items
    )
    accepted = (
        baseline.experiment.status == "completed"
        and candidate.experiment.status == "completed"
        and item_evidence_complete
        and gate.get("status") == "passed"
    )
    return LiveToolAcceptanceResult(
        accepted=accepted,
        completed_at=datetime.now(UTC).isoformat(),
        provider={
            "endpoint_origin": preflight.endpoint_origin,
            "configured_model": preflight.configured_model,
            "response_model": preflight.response_model,
            "challenge_verified": preflight.challenge_verified,
            "usage_present": preflight.total_tokens > 0,
        },
        dataset={
            "id": resources.dataset_id,
            "version_id": resources.dataset_version_id,
            "manifest_sha256": resources.manifest_sha256,
        },
        releases={
            "baseline_id": baseline_release.id,
            "candidate_id": candidate_release.id,
        },
        experiments={
            "baseline_id": baseline.experiment.id,
            "baseline_status": baseline.experiment.status,
            "candidate_id": candidate.experiment.id,
            "candidate_status": candidate.experiment.status,
            "evidence_complete": item_evidence_complete,
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
            "trace_ids": trace_ids,
            "workbench_links": {
                "Baseline Experiment": (
                    f"/?view=runs&run_id={baseline.experiment.id}"
                ),
                "Candidate Experiment": (
                    f"/?view=runs&run_id={candidate.experiment.id}"
                ),
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
        platform = PlatformConfig.from_environment()
        provider = LiveProviderConfig.from_environment()
        result = run_tool_acceptance(platform, provider)
    except (LiveAcceptanceConfigurationError, LiveProviderConfigurationError) as exc:
        print(f"live acceptance configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        platform_key = os.getenv("AGENT_EVAL_API_KEY", "")
        provider_key = os.getenv("LIVE_ACCEPTANCE_API_KEY", "")
        print(
            "live Tool acceptance failed: "
            + _safe_error(exc, (platform_key, provider_key)),
            file=sys.stderr,
        )
        return 3
    json_path, markdown_path = write_acceptance_artifacts(result)
    output = asdict(result)
    output["artifacts"] = {
        "json": str(json_path),
        "markdown": str(markdown_path),
    }
    print(json.dumps(output, ensure_ascii=True, indent=2))
    return 0 if result.accepted else 4


if __name__ == "__main__":
    raise SystemExit(main())
