"""Validation coverage for declarative release-gate policies."""

import pytest

from agent_eval_api.gate_policy import GatePolicyError, parse_gate_policy, policy_rules


def test_parse_policy_preserves_version_direction_severity_and_critical_scope() -> None:
    policy = parse_gate_policy(
        "version: release-v3\ngates:\n"
        "  - metric: task_success\n"
        "    operator: gte\n"
        "    threshold: 0.9\n"
        "    severity: block\n",
        available_metrics={"task_success"},
    )

    assert policy.version == "release-v3"
    assert policy.gates[0].operator.value == "gte"
    assert policy.gates[0].severity.value == "block"


def test_parse_policy_maps_critical_scope_to_case_ids() -> None:
    policy = parse_gate_policy(
        "version: v1\ngates:\n"
        "  - metric: task_success\n"
        "    operator: gte\n"
        "    threshold: 0.9\n"
        "    severity: block\n"
        "    scope: critical\n"
        "critical_tasks:\n"
        "  - checkout\n",
        available_metrics={"task_success"},
    )

    assert policy_rules(policy)[0].critical_task_ids == ["checkout"]


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("gates: [", "invalid YAML"),
        ("- not-a-mapping", "root must be a YAML mapping"),
        (
            "version: v1\ngates:\n"
            "  - metric: task_success\n"
            "    operator: nope\n"
            "    threshold: 1\n"
            "    severity: block",
            "invalid gate policy",
        ),
        (
            "version: v1\ngates:\n"
            "  - metric: task_success\n"
            "    operator: gte\n"
            "    threshold: '0.9'\n"
            "    severity: block",
            "threshold must be a number",
        ),
        (
            "version: v1\ngates:\n"
            "  - metric: task_success\n"
            "    operator: gte\n"
            "    threshold: 0.9\n"
            "    severity: block\n"
            "    scope: critical",
            "critical scope requires",
        ),
    ],
)
def test_parse_policy_rejects_invalid_documents(document: str, message: str) -> None:
    with pytest.raises(GatePolicyError, match=message):
        parse_gate_policy(document, available_metrics={"task_success"})


def test_parse_policy_rejects_unknown_metric() -> None:
    with pytest.raises(GatePolicyError, match="unknown gate metrics: typo_metric"):
        parse_gate_policy(
            "version: v1\ngates:\n"
            "  - metric: typo_metric\n"
            "    operator: gte\n"
            "    threshold: 0.9\n"
            "    severity: block\n",
            available_metrics={"task_success"},
        )

