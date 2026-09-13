"""Parse and validate versioned YAML release-gate policies."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

import yaml
from pydantic import ValidationError

from agent_eval_api.contracts import (
    GatePolicy,
    GatePolicyScope,
    RegressionGateRule,
)


class GatePolicyError(ValueError):
    """A policy cannot be trusted as a release decision input."""


# These names are derived from the evaluator and comparison contracts.  The
# run-specific evaluator names are added by the API before validation.
BUILT_IN_GATE_METRICS = frozenset(
    {
        "task_success",
        "tool_selection_accuracy",
        "tool_arguments_accuracy",
        "tool_execution_success",
        "retrieval_relevance",
        "retrieval_groundedness",
        "citation_accuracy",
        "format_compliance",
        "final_answer_quality",
        "p95_latency_ms",
        "average_latency_ms",
        "average_total_tokens",
        "average_cost_usd",
        "critical_regressions",
        "task_regressions",
    }
)


def parse_gate_policy(
    document: str,
    *,
    available_metrics: Collection[str] | None = None,
) -> GatePolicy:
    """Parse YAML and reject malformed or unknown release-gate inputs."""

    try:
        raw: Any = yaml.safe_load(document)
    except yaml.YAMLError as exc:
        raise GatePolicyError(f"invalid YAML: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise GatePolicyError("gate policy root must be a YAML mapping")
    _reject_non_numeric_thresholds(raw)
    try:
        policy = GatePolicy.model_validate(raw)
    except ValidationError as exc:
        messages = "; ".join(error["msg"] for error in exc.errors())
        raise GatePolicyError(f"invalid gate policy: {messages}") from exc

    if available_metrics is not None:
        allowed = set(available_metrics)
        unknown = sorted({rule.metric for rule in policy.gates if rule.metric not in allowed})
        if unknown:
            names = ", ".join(unknown)
            raise GatePolicyError(f"unknown gate metrics: {names}")
    return policy


def policy_rules(policy: GatePolicy) -> list[RegressionGateRule]:
    """Convert the declarative form to the current gate evaluator contract."""

    return [
        RegressionGateRule(
            metric_name=rule.metric,
            operator=rule.operator,
            threshold=rule.threshold,
            severity=rule.severity,
            critical_task_ids=(
                list(policy.critical_tasks)
                if rule.scope is GatePolicyScope.CRITICAL
                else []
            ),
        )
        for rule in policy.gates
    ]


def _reject_non_numeric_thresholds(raw: Mapping[str, Any]) -> None:
    gates = raw.get("gates")
    if not isinstance(gates, list):
        return
    for index, gate in enumerate(gates):
        if not isinstance(gate, Mapping) or "threshold" not in gate:
            continue
        threshold = gate["threshold"]
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise GatePolicyError(f"gate {index} threshold must be a number")
