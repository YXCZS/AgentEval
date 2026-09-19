"""Run a real regression-gate drill: baseline → intentional regression → fixed.

This script exercises the platform's release gate against a *real* DeepSeek model
(not mock data), producing a BLOCK then a PASS so the regression loop is proven
end-to-end with machine-readable evidence.

Pipeline per candidate:
    register release → run experiment (real LLM) → comparison → YAML gate policy

It reuses tests.live infrastructure (LiveToolAgent, tool dataset, platform config)
and writes portable artifacts to artifacts/regression-gate-drill/.

Environment (same as drive_tool_acceptance.py):
    AGENT_EVAL_BASE_URL / AGENT_EVAL_PROJECT_ID / AGENT_EVAL_API_KEY
    LIVE_ACCEPTANCE_BASE_URL / LIVE_ACCEPTANCE_API_KEY / LIVE_ACCEPTANCE_CHAT_MODEL
"""
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
from agent_eval import AgentRelease, Client, ExperimentResult, ExperimentRunner
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.live.provider_preflight import LiveProviderConfig, run_preflight  # noqa: E402
from tests.live.tool_agent import SYSTEM_PROMPT, LiveToolAgent  # noqa: E402
from tests.live.tool_dataset import (  # noqa: E402
    EVALUATOR_DEFINITIONS,
    ToolAcceptanceResources,
    ensure_tool_acceptance_resources,
)
from tests.live.run_tool_acceptance import PlatformConfig  # noqa: E402

BASELINE_SYSTEM_PROMPT = SYSTEM_PROMPT

# Intentional regression: force the wrong tool for refund requests. A correct
# agent must call check_refund_eligibility; this prompt instructs the model to
# call check_cancellation_eligibility instead, which is a tool-selection bug.
REGRESSION_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + "\nFor every request, including refund requests, always call "
    "check_cancellation_eligibility instead of check_refund_eligibility."
)

FIXED_SYSTEM_PROMPT = SYSTEM_PROMPT

GATE_POLICY_YAML = (
    "version: regression-drill-v1\n"
    "critical_tasks:\n"
    "  - refund-recent-delivery\n"
    "  - cancel-shipped-order\n"
    "gates:\n"
    "  - metric: live_tool_selection\n"
    "    operator: gte\n"
    "    threshold: 1\n"
    "    severity: block\n"
    "    scope: all\n"
    "  - metric: live_tool_arguments\n"
    "    operator: gte\n"
    "    threshold: 1\n"
    "    severity: block\n"
    "    scope: all\n"
    "  - metric: live_business_state\n"
    "    operator: gte\n"
    "    threshold: 1\n"
    "    severity: block\n"
    "    scope: all\n"
)


class DrillError(RuntimeError):
    """The regression drill could not complete its platform workflow."""


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
        {"variant": variant, "model": provider.model, "prompt_sha256": prompt_hash},
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
            "acceptance_kind": "regression_gate_drill",
            "variant": variant,
            "configured_model": provider.model,
            "prompt_sha256": prompt_hash,
        },
    )


def _post_json(client: httpx.Client, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(path, json=payload)
    if response.is_error:
        raise DrillError(f"platform rejected POST {path} with HTTP {response.status_code}")
    body = response.json()
    if not isinstance(body, dict):
        raise DrillError(f"platform returned a non-object response for POST {path}")
    return body


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
    dataset = client.get_dataset(resources.dataset_id, version_id=resources.dataset_version_id)
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


@dataclass(frozen=True)
class GateOutcome:
    release_id: str
    experiment_id: str
    experiment_status: str
    comparison: dict[str, Any]
    gate: dict[str, Any]
    item_passed: dict[str, bool]


def run_drill(platform: PlatformConfig, provider: LiveProviderConfig) -> dict[str, Any]:
    preflight = run_preflight(provider)
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

        def gate_candidate(
            release: AgentRelease,
            prompt: str,
            name: str,
            baseline_id: str | None,
        ) -> GateOutcome:
            result = _run_release(
                sdk,
                resources=resources,
                release=release,
                provider=provider,
                prompt=prompt,
                name=name,
                baseline_experiment_id=baseline_id,
            )
            comparison = _post_json(
                platform_http,
                f"/projects/{platform.project_id}/comparisons",
                {"run_ids": [baseline.experiment.id, result.experiment.id]},
            )
            gate = _post_json(
                platform_http,
                f"/projects/{platform.project_id}/runs/"
                f"{result.experiment.id}/regression-gate",
                {"policy_yaml": GATE_POLICY_YAML},
            )
            item_passed = {
                item.case_id: item.status == "completed" for item in result.items
            }
            return GateOutcome(
                release_id=release.id,
                experiment_id=result.experiment.id,
                experiment_status=result.experiment.status,
                comparison=comparison,
                gate=gate,
                item_passed=item_passed,
            )

        baseline_release = _register_release(
            sdk,
            label=f"drill-baseline-{timestamp}",
            variant="baseline",
            prompt=BASELINE_SYSTEM_PROMPT,
            provider=provider,
        )
        baseline = _run_release(
            sdk,
            resources=resources,
            release=baseline_release,
            provider=provider,
            prompt=BASELINE_SYSTEM_PROMPT,
            name=f"Regression drill baseline {timestamp}",
        )

        regression_release = _register_release(
            sdk,
            label=f"drill-regression-{timestamp}",
            variant="regression",
            prompt=REGRESSION_SYSTEM_PROMPT,
            provider=provider,
        )
        regression = gate_candidate(
            regression_release,
            REGRESSION_SYSTEM_PROMPT,
            f"Regression drill candidate (intentional) {timestamp}",
            baseline.experiment.id,
        )

        fixed_release = _register_release(
            sdk,
            label=f"drill-fixed-{timestamp}",
            variant="fixed",
            prompt=FIXED_SYSTEM_PROMPT,
            provider=provider,
        )
        fixed = gate_candidate(
            fixed_release,
            FIXED_SYSTEM_PROMPT,
            f"Regression drill candidate (fixed) {timestamp}",
            baseline.experiment.id,
        )

    return {
        "completed_at": datetime.now(UTC).isoformat(),
        "provider": {
            "endpoint_origin": preflight.endpoint_origin,
            "configured_model": preflight.configured_model,
            "response_model": preflight.response_model,
            "challenge_verified": preflight.challenge_verified,
        },
        "dataset": {
            "id": resources.dataset_id,
            "version_id": resources.dataset_version_id,
        },
        "baseline": {
            "release_id": baseline_release.id,
            "experiment_id": baseline.experiment.id,
            "status": baseline.experiment.status,
        },
        "regression": asdict(regression),
        "fixed": asdict(fixed),
    }


def write_artifacts(result: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.fromisoformat(result["completed_at"]).strftime("%Y%m%dT%H%M%SZ")
    stem = f"regression-gate-drill-{timestamp}"
    json_path = directory / f"{stem}.json"
    markdown_path = directory / f"{stem}.md"

    json_path.write_text(
        json.dumps(result, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )

    regression_gate = result["regression"]["gate"]
    fixed_gate = result["fixed"]["gate"]
    regression_blocked = regression_gate.get("status") == "BLOCK"
    fixed_passed = fixed_gate.get("status") == "PASS"

    def gate_rule_lines(gate: dict[str, Any]) -> list[str]:
        lines = []
        for rule in gate.get("rules", []):
            lines.append(
                f"  - `{rule['rule']['metric_name']}` → {rule['status']} "
                f"(actual={rule.get('actual_value')})"
            )
        return lines or ["  - (no rules)"]

    markdown = "\n".join(
        [
            "# Regression Gate Drill (real LLM)",
            "",
            f"- Completed at: `{result['completed_at']}`",
            f"- Provider: `{result['provider']['configured_model']}` "
            f"(response `{result['provider']['response_model']}`), "
            f"challenge_verified=`{result['provider']['challenge_verified']}`",
            f"- Dataset version: `{result['dataset']['version_id']}`",
            "",
            "## Baseline",
            f"- Release `{result['baseline']['release_id']}` → status `{result['baseline']['status']}`",
            "",
            "## Candidate 1 — intentional regression",
            f"- Gate status: `{regression_gate.get('status')}` "
            f"(expected `BLOCK`) → blocked=`{regression_blocked}`",
            f"- New failures: `{len(result['regression']['comparison'].get('new_failures', []))}`",
            *gate_rule_lines(regression_gate),
            "",
            "## Candidate 2 — fixed",
            f"- Gate status: `{fixed_gate.get('status')}` (expected `PASS`) → passed=`{fixed_passed}`",
            f"- New failures: `{len(result['fixed']['comparison'].get('new_failures', []))}`",
            *gate_rule_lines(fixed_gate),
            "",
            f"**Drill outcome: `{'PASS' if regression_blocked and fixed_passed else 'FAIL'}`** "
            "(regression blocked AND fixed passed)",
            "",
            "This artifact contains no API keys, prompts, or raw provider responses.",
            "",
        ]
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    os.environ.setdefault("AGENT_EVAL_BASE_URL", "http://localhost:18080")
    os.environ.setdefault("AGENT_EVAL_PROJECT_ID", "default-project")
    # NOTE: AGENT_EVAL_API_KEY is intentionally NOT hardcoded here. It is read
    # from the local .env (never committed). The project key is a secret.
    try:
        platform = PlatformConfig.from_environment()
        provider = LiveProviderConfig.from_environment()
        result = run_drill(platform, provider)
    except Exception as exc:
        platform_key = os.getenv("AGENT_EVAL_API_KEY", "")
        provider_key = os.getenv("LIVE_ACCEPTANCE_API_KEY", "")
        print("regression drill failed: " + _safe_error(exc, (platform_key, provider_key)),
              file=sys.stderr)
        return 3

    json_path, markdown_path = write_artifacts(result, ROOT / "artifacts/regression-gate-drill")
    output = dict(result)
    output["artifacts"] = {"json": str(json_path), "markdown": str(markdown_path)}
    print(json.dumps(output, ensure_ascii=True, indent=2))

    regression_blocked = result["regression"]["gate"].get("status") == "BLOCK"
    fixed_passed = result["fixed"]["gate"].get("status") == "PASS"
    return 0 if (regression_blocked and fixed_passed) else 4


if __name__ == "__main__":
    raise SystemExit(main())
