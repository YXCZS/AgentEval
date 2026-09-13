"""Contracts and capability declarations for integrations outside the first release."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Final

from agent_eval_api.contracts import (
    AdapterCapability,
    AdapterExecutionMode,
    AdapterExecutionResult,
    AdapterInvocation,
    AdapterKind,
    AdapterLifecycle,
    AgentType,
)


class FutureAdapterError(RuntimeError):
    """An explicit error for a planned adapter that has no execution backend yet."""

    def __init__(self, adapter_id: str, error_type: str, message: str) -> None:
        super().__init__(message)
        self.adapter_id = adapter_id
        self.error_type = error_type


class FutureAdapter(ABC):
    """Stable runner contract shared by future safety and environment adapters."""

    capability: AdapterCapability

    @abstractmethod
    async def execute(self, invocation: AdapterInvocation) -> AdapterExecutionResult:
        """Run an external adapter and normalize its result into platform scores."""


@dataclass(frozen=True)
class PlannedAdapter(FutureAdapter):
    capability: AdapterCapability

    async def execute(self, invocation: AdapterInvocation) -> AdapterExecutionResult:
        raise FutureAdapterError(
            self.capability.adapter_id,
            "adapter_unavailable",
            f"{self.capability.display_name} is declared for a future release and has no runner",
        )


def _capability(
    adapter_id: str,
    display_name: str,
    kind: AdapterKind,
    source_project: str,
    source_url: str,
    execution_mode: AdapterExecutionMode,
    supported_agent_types: list[AgentType],
    required_case_fields: list[str],
    result_metrics: list[str],
    *,
    requires_external_environment: bool,
    supports_ci: bool,
    config_schema: dict[str, object],
    limitations: list[str],
) -> AdapterCapability:
    return AdapterCapability(
        adapter_id=adapter_id,
        display_name=display_name,
        kind=kind,
        lifecycle=AdapterLifecycle.PLANNED,
        source_project=source_project,
        source_url=source_url,
        execution_mode=execution_mode,
        supported_agent_types=supported_agent_types,
        required_case_fields=required_case_fields,
        result_metrics=result_metrics,
        requires_external_environment=requires_external_environment,
        supports_ci=supports_ci,
        config_schema=config_schema,
        limitations=limitations,
    )


FUTURE_ADAPTER_CAPABILITIES: Final[tuple[AdapterCapability, ...]] = (
    _capability(
        "promptfoo-safety",
        "Promptfoo security assertions",
        AdapterKind.SAFETY_SCAN,
        "promptfoo",
        "https://github.com/promptfoo/promptfoo",
        AdapterExecutionMode.EXTERNAL_RUNNER,
        [AgentType.RAG, AgentType.TOOL, AgentType.CUSTOM],
        ["input"],
        ["assertion_pass_rate", "policy_violation_rate"],
        requires_external_environment=False,
        supports_ci=True,
        config_schema={"assertions": {"type": "array"}, "provider": {"type": "string"}},
        limitations=["Requires a pinned Promptfoo CLI or service runner."],
    ),
    _capability(
        "giskard-safety",
        "Giskard LLM scan",
        AdapterKind.SAFETY_SCAN,
        "Giskard",
        "https://github.com/Giskard-AI/giskard",
        AdapterExecutionMode.EXTERNAL_RUNNER,
        [AgentType.RAG, AgentType.CUSTOM],
        ["input"],
        ["vulnerability_count", "scan_pass_rate"],
        requires_external_environment=False,
        supports_ci=True,
        config_schema={"scan_profile": {"type": "string"}},
        limitations=["Model and detector support depends on the installed Giskard version."],
    ),
    _capability(
        "garak-safety",
        "NVIDIA garak probes",
        AdapterKind.SAFETY_SCAN,
        "NVIDIA garak",
        "https://github.com/NVIDIA/garak",
        AdapterExecutionMode.EXTERNAL_RUNNER,
        [AgentType.RAG, AgentType.CUSTOM],
        ["input"],
        ["probe_pass_rate", "detector_failure_rate"],
        requires_external_environment=False,
        supports_ci=True,
        config_schema={"probes": {"type": "array"}, "detectors": {"type": "array"}},
        limitations=["Probe execution may make many model calls and incur provider cost."],
    ),
    _capability(
        "agentbench",
        "AgentBench tasks",
        AdapterKind.BENCHMARK,
        "AgentBench",
        "https://github.com/THUDM/AgentBench",
        AdapterExecutionMode.EXTERNAL_ENVIRONMENT,
        [AgentType.TOOL, AgentType.CUSTOM],
        ["input"],
        ["task_success", "average_reward"],
        requires_external_environment=True,
        supports_ci=False,
        config_schema={"task": {"type": "string"}, "environment_url": {"type": "string"}},
        limitations=["Requires a separately provisioned benchmark environment and task runner."],
    ),
    _capability(
        "webarena",
        "WebArena browser tasks",
        AdapterKind.BENCHMARK,
        "WebArena",
        "https://github.com/web-arena-x/webarena",
        AdapterExecutionMode.EXTERNAL_ENVIRONMENT,
        [AgentType.TOOL, AgentType.CUSTOM],
        ["input", "metadata"],
        ["task_success", "completion_rate"],
        requires_external_environment=True,
        supports_ci=False,
        config_schema={"task_ids": {"type": "array"}, "browser_endpoint": {"type": "string"}},
        limitations=["Needs isolated browser-backed websites and a browser agent runner."],
    ),
    _capability(
        "osworld",
        "OSWorld desktop tasks",
        AdapterKind.BENCHMARK,
        "OSWorld",
        "https://github.com/xlang-ai/OSWorld",
        AdapterExecutionMode.EXTERNAL_ENVIRONMENT,
        [AgentType.TOOL, AgentType.CUSTOM],
        ["input", "metadata"],
        ["task_success", "step_efficiency"],
        requires_external_environment=True,
        supports_ci=False,
        config_schema={"task_ids": {"type": "array"}, "vm_endpoint": {"type": "string"}},
        limitations=["Requires a disposable desktop VM and external task evaluator."],
    ),
)


FUTURE_ADAPTERS: Final[dict[str, PlannedAdapter]] = {
    capability.adapter_id: PlannedAdapter(capability=capability)
    for capability in FUTURE_ADAPTER_CAPABILITIES
}


def list_future_adapter_capabilities() -> list[AdapterCapability]:
    return list(FUTURE_ADAPTER_CAPABILITIES)


def get_future_adapter(adapter_id: str) -> FutureAdapter:
    try:
        return FUTURE_ADAPTERS[adapter_id]
    except KeyError as exc:
        raise FutureAdapterError(
            adapter_id, "invalid_configuration", f"unknown future adapter: {adapter_id}"
        ) from exc
