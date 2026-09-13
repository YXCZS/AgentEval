"""Pydantic contracts for the evaluation workbench.

The models intentionally keep domain payloads as JSON objects. This lets the
platform support RAG, tool and custom agents without flattening their different
inputs into unrelated APIs.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, model_validator

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None
JsonObject = dict[str, Any]


class ContractModel(BaseModel):
    """Reject accidental API drift while allowing explicit JSON payload fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AgentType(StrEnum):
    RAG = "rag"
    TOOL = "tool"
    CUSTOM = "custom"


class ExperimentExecutionMode(StrEnum):
    """Where a real Agent execution is controlled."""

    SDK_TASK = "sdk_task"
    OTEL = "otel"
    REMOTE_UPLOAD = "remote_upload"
    REMOTE_TRIGGER = "remote_trigger"


class EvidencePolicy(StrEnum):
    """Minimum server-verified evidence required for an Experiment Item."""

    TRACE_REQUIRED = "trace_required"
    LLM_REQUIRED = "llm_required"
    TOOL_TRAJECTORY_REQUIRED = "tool_trajectory_required"
    RAG_TRAJECTORY_REQUIRED = "rag_trajectory_required"


class EvaluatorType(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM_JUDGE = "llm_judge"
    ADAPTER = "adapter"
    HUMAN = "human"


class ProviderKind(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"


class ProviderConnectionStatus(StrEnum):
    PENDING_VALIDATION = "pending_validation"
    ACTIVE = "active"
    ERROR = "error"
    DISABLED = "disabled"


class JudgeSamplingParameters(ContractModel):
    """Versioned sampling controls for a platform-managed Judge request."""

    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, gt=0.0, le=1.0)
    max_tokens: int = Field(default=1000, ge=1, le=65536)
    seed: int | None = None


class AdapterKind(StrEnum):
    SAFETY_SCAN = "safety_scan"
    BENCHMARK = "benchmark"


class AdapterLifecycle(StrEnum):
    PLANNED = "planned"
    EXPERIMENTAL = "experimental"
    AVAILABLE = "available"


class AdapterExecutionMode(StrEnum):
    EXTERNAL_RUNNER = "external_runner"
    EXTERNAL_ENVIRONMENT = "external_environment"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScoreStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    MISSING = "missing"
    ERROR = "error"
    NOT_RUN = "not_run"


class ScoreSource(StrEnum):
    """Origin of a score, kept separate so evidence is never silently replaced."""

    AUTOMATED = "automated"
    DETERMINISTIC = "deterministic"
    LLM_JUDGE = "llm_judge"
    HUMAN = "human"
    ADAPTER = "adapter"


class AnnotationStatus(StrEnum):
    PENDING = "pending"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class ScoreDirection(StrEnum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


class RegressionGateStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"
    INCOMPLETE = "incomplete"
    PASS = "PASS"
    WARNING = "WARNING"
    BLOCK = "BLOCK"
    RELEASE_INDETERMINATE = "INDETERMINATE"
    RELEASE_INCOMPLETE = "INCOMPLETE"


class GatePolicyOperator(StrEnum):
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN_OR_EQUAL = "lte"
    GREATER_THAN = "gt"
    LESS_THAN = "lt"
    EQUAL = "eq"


class GatePolicySeverity(StrEnum):
    BLOCK = "block"
    WARNING = "warning"


class GatePolicyScope(StrEnum):
    ALL = "all"
    CRITICAL = "critical"


class TraceSpanKind(StrEnum):
    AGENT = "agent"
    PROMPT = "prompt"
    LLM = "llm"
    TOOL = "tool"
    TOOL_RESULT = "tool_result"
    RETRIEVAL = "retrieval"
    GUARDRAIL = "guardrail"
    EVALUATOR = "evaluator"


class AgentRelease(ContractModel):
    """An immutable execution identity for a user-owned Agent runtime."""

    id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    version: int = Field(gt=0)
    label: str = Field(min_length=1, max_length=100)
    agent_type: AgentType
    release_identity: str = Field(min_length=1, max_length=200)
    source_revision: str | None = Field(default=None, min_length=1, max_length=200)
    metadata: JsonObject = Field(default_factory=dict)
    enabled: bool = True
    created_at: datetime


class ExpectedToolCall(ContractModel):
    name: str = Field(min_length=1, max_length=200)
    arguments: JsonObject = Field(default_factory=dict)
    order: int | None = Field(default=None, ge=0)


class ChatMessage(ContractModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: JsonValue
    name: str | None = Field(default=None, min_length=1, max_length=200)


class ExternalScoreProvenance(ContractModel):
    """Evidence identifying how an LLM Judge produced a score."""

    source: Literal["external_judge", "platform_provider"] = "external_judge"
    protocol: Literal[
        "signed_http_json_v1", "openai_compatible_chat_completions"
    ] | None = None
    connection_id: str | None = Field(default=None, min_length=1, max_length=128)
    evaluator_version: str = Field(min_length=1, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    model_release: str | None = Field(default=None, max_length=200)
    rubric_version: str | None = Field(default=None, max_length=100)
    prompt_template_version: str | None = Field(default=None, max_length=100)
    metadata: JsonObject = Field(default_factory=dict)


class ExternalJudgeRequest(ContractModel):
    """Stable JSON request sent to a user-managed external LLM Judge."""

    run_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=128)
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)
    metric_name: str = Field(min_length=1, max_length=200)
    evaluator_version: str = Field(min_length=1, max_length=100)
    rubric: str = Field(min_length=1)
    input: JsonValue
    expected_output: JsonValue = None
    actual_output: JsonValue = None
    criteria: list[str] = Field(default_factory=list)
    tool_calls: list[ExpectedToolCall] = Field(default_factory=list)
    trace: JsonObject | None = None
    metadata: JsonObject = Field(default_factory=dict)


class ExternalJudgeResponse(ContractModel):
    """Normalized score and provenance returned by an external LLM Judge."""

    score: float
    passed: bool | None = None
    label: str | None = None
    explanation: str = Field(min_length=1)
    evidence: list[JsonObject] = Field(default_factory=list)
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)
    provenance: ExternalScoreProvenance
    extensions: JsonObject = Field(default_factory=dict)


class RetrievalContext(ContractModel):
    content: str = Field(min_length=1)
    document_id: str | None = Field(default=None, min_length=1, max_length=200)
    metadata: JsonObject = Field(default_factory=dict)


class DatasetCase(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    input: JsonValue
    variables: JsonObject = Field(default_factory=dict)
    expected_output: JsonValue = None
    output_schema: JsonObject | None = None
    criteria: list[str] = Field(default_factory=list)
    expected_tools: list[ExpectedToolCall] = Field(default_factory=list)
    expected_state: JsonObject | None = None
    retrieval_context: list[RetrievalContext] = Field(default_factory=list)
    messages: list[ChatMessage] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)
    source_trace_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_span_ids: list[str] = Field(default_factory=list)
    source_mapping: JsonObject = Field(default_factory=dict)


class DatasetVersion(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    version: int = Field(gt=0)
    cases: list[DatasetCase] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)
    created_at: datetime

    @model_validator(mode="after")
    def validate_unique_case_ids(self) -> DatasetVersion:
        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("dataset case ids must be unique within a version")
        return self


class Dataset(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    current_version_id: str | None = Field(default=None, min_length=1, max_length=128)
    created_at: datetime
    updated_at: datetime


class RemoteTrigger(ContractModel):
    """Public Dataset-scoped trigger metadata; the signing secret is write-only."""

    id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    trigger_url: HttpUrl
    enabled: bool
    signature_header: str = Field(
        default="X-Agent-Eval-Trigger-Signature", min_length=1, max_length=100
    )
    secret_mask: str = Field(min_length=1, max_length=64)
    secret_key_id: str = Field(min_length=1, max_length=128)
    created_at: datetime
    updated_at: datetime


class RemoteTriggerCreateRequest(ContractModel):
    trigger_url: HttpUrl
    enabled: bool = True

    @model_validator(mode="after")
    def validate_trigger_url(self) -> Self:
        from urllib.parse import urlsplit

        parsed = urlsplit(str(self.trigger_url))
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("trigger_url must not contain credentials, query, or fragment")
        return self


class RemoteTriggerUpdateRequest(ContractModel):
    trigger_url: HttpUrl | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_trigger_url(self) -> Self:
        if self.trigger_url is not None:
            from urllib.parse import urlsplit

            parsed = urlsplit(str(self.trigger_url))
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError(
                    "trigger_url must not contain credentials, query, or fragment"
                )
        return self


class RemoteTriggerCreated(RemoteTrigger):
    """The plaintext signing secret is returned only in this create response."""

    signing_secret: str = Field(min_length=1, max_length=512)


class RemoteTriggerDeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERING = "delivering"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"


class RemoteTriggerDelivery(ContractModel):
    """Safe delivery observability for a signed remote Experiment trigger."""

    id: str = Field(min_length=1, max_length=128)
    trigger_id: str = Field(min_length=1, max_length=128)
    experiment_id: str = Field(min_length=1, max_length=128)
    delivery_id: str = Field(min_length=1, max_length=128)
    status: RemoteTriggerDeliveryStatus
    attempt_count: int = Field(ge=0, le=3)
    last_http_status: int | None = Field(default=None, ge=100, le=599)
    last_error_type: str | None = Field(default=None, max_length=64)
    last_error_message: str | None = Field(default=None, max_length=200)
    accepted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class EvaluatorVersion(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    evaluator_type: EvaluatorType
    requires: list[str] = Field(default_factory=list)
    supported_agent_types: list[AgentType] = Field(min_length=1)
    score_min: float | None = None
    score_max: float | None = None
    direction: ScoreDirection
    default_threshold: float | None = None
    rubric: str | None = None
    evaluator_connection_id: str | None = Field(default=None, max_length=128)
    provider_connection_id: str | None = Field(default=None, max_length=128)
    judge_model: str | None = Field(default=None, max_length=200)
    prompt_template: str | None = None
    output_schema: JsonObject | None = None
    sampling_parameters: JudgeSamplingParameters | None = None
    config: JsonObject = Field(default_factory=dict)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_score_range(self) -> EvaluatorVersion:
        if any(not field_name for field_name in self.requires) or len(self.requires) != len(
            set(self.requires)
        ):
            raise ValueError("requires must contain unique non-empty field names")
        if self.score_min is not None and self.score_max is not None:
            if self.score_min >= self.score_max:
                raise ValueError("score_min must be lower than score_max")
            if self.default_threshold is not None and not (
                self.score_min <= self.default_threshold <= self.score_max
            ):
                raise ValueError("default_threshold must be within score range")
        return self


class EvaluatorVersionCreateRequest(ContractModel):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    evaluator_type: EvaluatorType
    requires: list[str] = Field(default_factory=list)
    supported_agent_types: list[AgentType] = Field(min_length=1)
    score_min: float | None = None
    score_max: float | None = None
    direction: ScoreDirection
    default_threshold: float | None = None
    rubric: str | None = None
    evaluator_connection_id: str | None = Field(default=None, max_length=128)
    provider_connection_id: str | None = Field(default=None, max_length=128)
    judge_model: str | None = Field(default=None, min_length=1, max_length=200)
    prompt_template: str | None = Field(default=None, min_length=1)
    output_schema: JsonObject | None = None
    sampling_parameters: JudgeSamplingParameters | None = None
    config: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_score_range(self) -> EvaluatorVersionCreateRequest:
        EvaluatorVersion(
            id="validation-only",
            name=self.name,
            version=self.version,
            evaluator_type=self.evaluator_type,
            requires=self.requires,
            supported_agent_types=self.supported_agent_types,
            score_min=self.score_min,
            score_max=self.score_max,
            direction=self.direction,
            default_threshold=self.default_threshold,
            rubric=self.rubric,
            evaluator_connection_id=self.evaluator_connection_id,
            provider_connection_id=self.provider_connection_id,
            judge_model=self.judge_model,
            prompt_template=self.prompt_template,
            output_schema=self.output_schema,
            sampling_parameters=self.sampling_parameters,
            config=self.config,
        )
        return self


class AdapterCapability(ContractModel):
    """Declared boundary for integrations that need a separate runner or environment."""

    adapter_id: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    kind: AdapterKind
    lifecycle: AdapterLifecycle = AdapterLifecycle.PLANNED
    source_project: str = Field(min_length=1, max_length=200)
    source_url: str = Field(min_length=1, max_length=500)
    execution_mode: AdapterExecutionMode
    supported_agent_types: list[AgentType] = Field(min_length=1)
    required_case_fields: list[str] = Field(default_factory=list)
    required_trace_kinds: list[TraceSpanKind] = Field(default_factory=list)
    result_metrics: list[str] = Field(min_length=1)
    requires_external_environment: bool = False
    supports_ci: bool = True
    config_schema: JsonObject = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_capability_lists(self) -> AdapterCapability:
        if len(self.supported_agent_types) != len(set(self.supported_agent_types)):
            raise ValueError("supported_agent_types must be unique")
        if any(not field_name for field_name in self.required_case_fields):
            raise ValueError("required_case_fields must contain non-empty names")
        if any(not metric for metric in self.result_metrics):
            raise ValueError("result_metrics must contain non-empty names")
        return self


class AdapterInvocation(ContractModel):
    """Canonical input passed to a future safety or benchmark runner."""

    adapter_id: str = Field(min_length=1, max_length=100)
    run_id: str = Field(min_length=1, max_length=128)
    agent_version_id: str = Field(min_length=1, max_length=128)
    dataset_version_id: str = Field(min_length=1, max_length=128)
    cases: list[DatasetCase] = Field(min_length=1)
    config: JsonObject = Field(default_factory=dict)


class AdapterScoreResult(ContractModel):
    metric_name: str = Field(min_length=1, max_length=200)
    value: float | None = None
    passed: bool | None = None
    label: str | None = None
    explanation: str | None = None
    evidence: list[JsonObject] = Field(default_factory=list)
    raw_result: JsonValue | None = None


class AdapterExecutionResult(ContractModel):
    adapter_id: str = Field(min_length=1, max_length=100)
    adapter_version: str = Field(min_length=1, max_length=100)
    status: Literal["completed", "failed", "not_available"]
    scores: list[AdapterScoreResult] = Field(default_factory=list)
    raw_result: JsonValue | None = None
    error_type: str | None = None
    error_message: str | None = None


class EvaluationExecutionOptions(ContractModel):
    """Run-level controls frozen into an Experiment definition."""

    repetitions: int = Field(default=1, ge=1, le=100)
    concurrency: int = Field(default=4, ge=1, le=100)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.2, ge=0.0, le=30.0)


class EvaluationRun(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="Experiment", min_length=1, max_length=200)
    agent_version_id: str = Field(min_length=1, max_length=128)
    dataset_version_id: str = Field(min_length=1, max_length=128)
    evaluator_version_ids: list[str] = Field(min_length=1)
    execution_mode: ExperimentExecutionMode = ExperimentExecutionMode.SDK_TASK
    evidence_policy: EvidencePolicy = EvidencePolicy.TRACE_REQUIRED
    status: RunStatus = RunStatus.QUEUED
    total_cases: int = Field(default=0, ge=0)
    completed_cases: int = Field(default=0, ge=0)
    failed_cases: int = Field(default=0, ge=0)
    baseline_run_id: str | None = Field(default=None, min_length=1, max_length=128)
    execution_options: EvaluationExecutionOptions = Field(
        default_factory=lambda: EvaluationExecutionOptions()
    )
    configuration_snapshot: JsonObject = Field(default_factory=dict)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def validate_case_counts(self) -> EvaluationRun:
        if self.completed_cases + self.failed_cases > self.total_cases:
            raise ValueError("completed and failed cases cannot exceed total_cases")
        if len(self.evaluator_version_ids) != len(set(self.evaluator_version_ids)):
            raise ValueError("evaluator_version_ids must be unique")
        return self


class EvaluationRunCreateRequest(ContractModel):
    name: str = Field(default="Experiment", min_length=1, max_length=200)
    agent_version_id: str = Field(min_length=1, max_length=128)
    dataset_version_id: str = Field(min_length=1, max_length=128)
    evaluator_version_ids: list[str] = Field(min_length=1)
    execution_mode: ExperimentExecutionMode = ExperimentExecutionMode.SDK_TASK
    evidence_policy: EvidencePolicy = EvidencePolicy.TRACE_REQUIRED
    baseline_run_id: str | None = Field(default=None, min_length=1, max_length=128)
    execution_options: EvaluationExecutionOptions = Field(
        default_factory=lambda: EvaluationExecutionOptions()
    )

    @model_validator(mode="after")
    def validate_unique_evaluators(self) -> EvaluationRunCreateRequest:
        if len(self.evaluator_version_ids) != len(set(self.evaluator_version_ids)):
            raise ValueError("evaluator_version_ids must be unique")
        return self


class CaseExecution(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=128)
    status: ExecutionStatus = ExecutionStatus.QUEUED
    attempt: int = Field(default=0, ge=0)
    output: JsonValue = None
    tool_calls: list[ExpectedToolCall] = Field(default_factory=list)
    usage: JsonObject = Field(default_factory=dict)
    error_type: str | None = None
    error_message: str | None = None
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ExperimentItemAttempt(ContractModel):
    """One immutable attempt to execute a Dataset Case in an Experiment."""

    id: str = Field(min_length=1, max_length=128)
    experiment_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=128)
    repetition: int = Field(ge=1)
    attempt: int = Field(ge=1)
    external_run_id: str = Field(min_length=1, max_length=200)
    status: ExecutionStatus = ExecutionStatus.QUEUED
    output: JsonValue = None
    usage: JsonObject = Field(default_factory=dict)
    runtime_metadata: JsonObject = Field(default_factory=dict)
    error_type: str | None = Field(default=None, max_length=100)
    error_message: str | None = None
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)
    evidence_status: Literal["pending", "complete", "incomplete", "not_required"] = (
        "pending"
    )
    evidence_reasons: list[str] = Field(default_factory=list)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ExperimentItemStartRequest(ContractModel):
    case_id: str = Field(min_length=1, max_length=128)
    repetition: int = Field(default=1, ge=1)
    attempt: int = Field(default=1, ge=1)
    external_run_id: str = Field(min_length=1, max_length=200)
    expected_status: Literal["queued"] = "queued"
    runtime_metadata: JsonObject = Field(default_factory=dict)


class ExperimentItemCompleteRequest(ContractModel):
    expected_status: Literal["running"] = "running"
    output: JsonValue
    usage: JsonObject = Field(default_factory=dict)
    runtime_metadata: JsonObject = Field(default_factory=dict)
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def require_output(self) -> ExperimentItemCompleteRequest:
        if self.output is None:
            raise ValueError("completed items require a non-null output")
        return self


class ExperimentItemFailRequest(ContractModel):
    expected_status: Literal["running"] = "running"
    error_type: str = Field(min_length=1, max_length=100)
    error_message: str = Field(min_length=1)
    output: JsonValue = None
    usage: JsonObject = Field(default_factory=dict)
    runtime_metadata: JsonObject = Field(default_factory=dict)
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)


class ExperimentItemCancelRequest(ContractModel):
    expected_status: Literal["running"] = "running"
    runtime_metadata: JsonObject = Field(default_factory=dict)


class ExperimentManifestItem(ContractModel):
    """One frozen Dataset Case and the attempts currently recorded for it."""

    case: DatasetCase
    attempts: list[ExperimentItemAttempt] = Field(default_factory=list)


class ExperimentManifestPage(ContractModel):
    experiment_id: str = Field(min_length=1, max_length=128)
    dataset_version_id: str = Field(min_length=1, max_length=128)
    items: list[ExperimentManifestItem] = Field(default_factory=list)
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    next_offset: int | None = Field(default=None, ge=0)


class SdkContractResponse(ContractModel):
    contract: Literal["agent-eval-sdk"] = "agent-eval-sdk"
    major_version: Literal[1] = 1
    service_version: str = Field(min_length=1)


class EvaluationRunDetail(EvaluationRun):
    case_executions: list[CaseExecution] = Field(default_factory=list)


class ExperimentPage(ContractModel):
    """A project-scoped, server-filtered page of immutable Experiments."""

    items: list[EvaluationRun] = Field(default_factory=list)
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    next_offset: int | None = Field(default=None, ge=0)


class TraceSpan(ContractModel):
    span_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    parent_span_id: str | None = Field(default=None, min_length=1, max_length=128)
    kind: TraceSpanKind
    name: str = Field(min_length=1, max_length=200)
    status: ExecutionStatus
    started_at: datetime
    ended_at: datetime | None = None
    input: JsonValue = None
    output: JsonValue = None
    error: JsonObject | None = None
    usage: JsonObject = Field(default_factory=dict)
    cost: JsonValue = None
    attributes: JsonObject = Field(default_factory=dict)
    extensions: JsonObject = Field(default_factory=dict)


class Trace(ContractModel):
    trace_id: str = Field(min_length=1, max_length=128)
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    case_id: str | None = Field(default=None, min_length=1, max_length=128)
    status: ExecutionStatus
    spans: list[TraceSpan] = Field(default_factory=list)
    scores: list[Score] = Field(default_factory=list)
    source: str = Field(default="platform", min_length=1, max_length=100)
    extensions: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_span_trace_ids(self) -> Trace:
        if any(span.trace_id != self.trace_id for span in self.spans):
            raise ValueError("all trace spans must reference the containing trace")
        span_ids = [span.span_id for span in self.spans]
        if len(span_ids) != len(set(span_ids)):
            raise ValueError("trace span ids must be unique")
        parent_by_span = {span.span_id: span.parent_span_id for span in self.spans}
        for span in self.spans:
            parent_span_id = span.parent_span_id
            if parent_span_id is not None and parent_span_id not in parent_by_span:
                raise ValueError("trace span parent must exist in the containing trace")
            seen: set[str] = set()
            while parent_span_id is not None:
                if parent_span_id in seen:
                    raise ValueError("trace spans must not contain parent cycles")
                seen.add(parent_span_id)
                parent_span_id = parent_by_span[parent_span_id]
        return self


class TraceIngestRequest(ContractModel):
    """Accept either the platform schema or an external trace payload."""

    source: str = Field(default="external", min_length=1, max_length=100)
    trace: Trace | None = None
    payload: JsonObject | None = None

    @model_validator(mode="after")
    def validate_single_payload(self) -> TraceIngestRequest:
        if (self.trace is None) == (self.payload is None):
            raise ValueError("provide exactly one of trace or payload")
        return self


class TraceFieldSelection(ContractModel):
    span_id: str = Field(min_length=1, max_length=128)
    field: Literal["input", "output", "attributes", "extensions"]
    attribute_key: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_attribute_selection(self) -> TraceFieldSelection:
        if self.attribute_key is not None and self.field not in {"attributes", "extensions"}:
            raise ValueError("attribute_key requires attributes or extensions")
        return self


class TraceToDatasetCaseRequest(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    input: TraceFieldSelection | None = None
    expected_output: TraceFieldSelection | None = None
    expected_state: TraceFieldSelection | None = None
    tool_span_ids: list[str] | None = None
    metadata: JsonObject = Field(default_factory=dict)
    metadata_mapping: dict[str, TraceFieldSelection] = Field(default_factory=dict)


class TraceSummary(ContractModel):
    trace_id: str = Field(min_length=1, max_length=128)
    run_id: str | None = None
    case_id: str | None = None
    status: ExecutionStatus
    source: str = Field(min_length=1, max_length=100)
    span_count: int = Field(ge=0)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime


class TraceSummaryPage(ContractModel):
    """A project-scoped, server-filtered page of Trace summaries."""

    items: list[TraceSummary] = Field(default_factory=list)
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    next_offset: int | None = Field(default=None, ge=0)


class TraceTimelineSpan(ContractModel):
    span_id: str = Field(min_length=1, max_length=128)
    parent_span_id: str | None = None
    kind: TraceSpanKind
    name: str = Field(min_length=1, max_length=200)
    status: ExecutionStatus
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: float | None = Field(default=None, ge=0.0)
    depth: int = Field(ge=0)


class TraceTimeline(ContractModel):
    trace_id: str = Field(min_length=1, max_length=128)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    spans: list[TraceTimelineSpan] = Field(default_factory=list)


class Score(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    case_id: str | None = Field(default=None, min_length=1, max_length=128)
    experiment_item_id: str | None = Field(default=None, min_length=1, max_length=128)
    repetition: int = Field(default=1, ge=1)
    attempt: int | None = Field(default=None, ge=1)
    metric_name: str = Field(min_length=1, max_length=200)
    evaluator_version_id: str = Field(min_length=1, max_length=128)
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)
    span_id: str | None = Field(default=None, min_length=1, max_length=128)
    source: ScoreSource = ScoreSource.AUTOMATED
    status: ScoreStatus
    value: float | None = None
    label: str | None = None
    passed: bool | None = None
    explanation: str | None = None
    evidence: list[JsonObject] = Field(default_factory=list)
    rubric: str | None = None
    judge_model: str | None = None
    provenance: ExternalScoreProvenance | None = None
    threshold: float | None = None
    direction: ScoreDirection
    raw_response: JsonValue = None
    raw_result: JsonValue = None

    @model_validator(mode="after")
    def validate_result(self) -> Score:
        if self.status in {ScoreStatus.PASSED, ScoreStatus.FAILED}:
            if self.value is None and self.label is None:
                raise ValueError("passed or failed scores require value or label")
            if self.passed is None:
                raise ValueError("passed or failed scores require an explicit passed flag")
        if self.status in {ScoreStatus.MISSING, ScoreStatus.ERROR, ScoreStatus.NOT_RUN}:
            if self.passed is True:
                raise ValueError("missing, error and not_run scores cannot pass")
        return self


class OnlineScoreRequest(ContractModel):
    """Normalized score submitted for an already-ingested Trace or Span."""

    evaluator_version_id: str = Field(min_length=1, max_length=128)
    source: Literal["deterministic", "llm_judge", "adapter"]
    span_id: str | None = Field(default=None, min_length=1, max_length=128)
    status: ScoreStatus
    value: float | None = None
    label: str | None = Field(default=None, min_length=1, max_length=100)
    passed: bool | None = None
    explanation: str | None = None
    evidence: list[JsonObject] = Field(default_factory=list)
    judge_model: str | None = Field(default=None, max_length=200)
    provenance: ExternalScoreProvenance | None = None
    raw_response: JsonValue = None
    raw_result: JsonValue = None

    @model_validator(mode="after")
    def validate_score(self) -> OnlineScoreRequest:
        if self.status in {ScoreStatus.PASSED, ScoreStatus.FAILED}:
            if self.value is None and self.label is None:
                raise ValueError("passed or failed scores require value or label")
            if self.passed is None:
                raise ValueError("passed or failed scores require an explicit passed flag")
        if self.status in {ScoreStatus.MISSING, ScoreStatus.ERROR, ScoreStatus.NOT_RUN}:
            if self.passed is True:
                raise ValueError("missing, error and not_run scores cannot pass")
        return self


# Score is declared after Trace so the nested response model can be resolved.
Trace.model_rebuild()


class AnnotationQueueCreateRequest(ContractModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    evaluator_version_id: str = Field(min_length=1, max_length=128)


class AnnotationQueue(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    evaluator_version_id: str = Field(min_length=1, max_length=128)
    created_at: datetime


class AnnotationQueueItemCreateRequest(ContractModel):
    run_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=128)


class AnnotationQueueItem(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    queue_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=128)
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)
    status: AnnotationStatus
    created_at: datetime
    completed_at: datetime | None = None


class HumanScoreRequest(ContractModel):
    value: float | None = None
    label: str | None = Field(default=None, min_length=1, max_length=100)
    passed: bool
    explanation: str | None = None
    evidence: list[JsonObject] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result(self) -> HumanScoreRequest:
        if self.value is None and self.label is None:
            raise ValueError("human score requires value or label")
        return self


class HumanScoreAudit(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    score_id: str = Field(min_length=1, max_length=128)
    action: Literal["created", "updated"]
    reviewer: str = Field(min_length=1, max_length=200)
    previous_value: JsonObject | None = None
    new_value: JsonObject
    created_at: datetime


class AggregateMetric(ContractModel):
    metric_name: str = Field(min_length=1, max_length=200)
    evaluator_version_id: str = Field(min_length=1, max_length=128)
    valid_count: int = Field(default=0, ge=0)
    missing_count: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    passed_count: int = Field(default=0, ge=0)
    average: float | None = None
    pass_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    aggregation: Literal["mean", "pass_rate", "sum", "min", "max"]
    threshold: float | None = None
    direction: ScoreDirection

    @model_validator(mode="after")
    def validate_aggregate_counts(self) -> AggregateMetric:
        if self.passed_count > self.valid_count:
            raise ValueError("passed_count cannot exceed valid_count")
        if self.pass_rate is not None and self.valid_count == 0:
            raise ValueError("pass_rate requires at least one valid score")
        return self


class EvaluationReportSummary(ContractModel):
    run_id: str = Field(min_length=1, max_length=128)
    status: RunStatus
    agent_version_id: str = Field(min_length=1, max_length=128)
    dataset_version_id: str = Field(min_length=1, max_length=128)
    total_cases: int = Field(ge=0)
    completed_cases: int = Field(ge=0)
    failed_cases: int = Field(ge=0)
    metrics: list[AggregateMetric] = Field(default_factory=list)
    created_at: datetime
    finished_at: datetime | None = None


class EvaluationReportCase(ContractModel):
    case_id: str = Field(min_length=1, max_length=128)
    metadata: JsonObject = Field(default_factory=dict)
    execution_status: ExecutionStatus
    error_type: str | None = None
    error_message: str | None = None
    output: JsonValue = None
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)
    scores: list[Score] = Field(default_factory=list)


class EvaluationReport(ContractModel):
    run_id: str = Field(min_length=1, max_length=128)
    status: RunStatus
    total_cases: int = Field(ge=0)
    matched_cases: int = Field(ge=0)
    filters: JsonObject = Field(default_factory=dict)
    metrics: list[AggregateMetric] = Field(default_factory=list)
    cases: list[EvaluationReportCase] = Field(default_factory=list)
    generated_at: datetime


class ComparisonRequest(ContractModel):
    run_ids: list[str] = Field(min_length=2, max_length=10)

    @model_validator(mode="after")
    def validate_unique_runs(self) -> ComparisonRequest:
        if any(not run_id for run_id in self.run_ids):
            raise ValueError("run_ids must contain non-empty ids")
        if len(self.run_ids) != len(set(self.run_ids)):
            raise ValueError("run_ids must be unique")
        return self


class ComparisonRun(ContractModel):
    run_id: str = Field(min_length=1, max_length=128)
    agent_version_id: str = Field(min_length=1, max_length=128)
    agent_version: JsonObject = Field(default_factory=dict)
    dataset_version_id: str = Field(min_length=1, max_length=128)
    status: RunStatus
    total_cases: int = Field(ge=0)
    completed_cases: int = Field(ge=0)
    failed_cases: int = Field(ge=0)
    created_at: datetime
    finished_at: datetime | None = None


class ComparisonMetricPoint(ContractModel):
    run_id: str = Field(min_length=1, max_length=128)
    average: float | None = None
    pass_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    valid_count: int = Field(default=0, ge=0)
    missing_count: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    passed_count: int = Field(default=0, ge=0)
    missing_case_ids: list[str] = Field(default_factory=list)
    error_case_ids: list[str] = Field(default_factory=list)
    delta_average: float | None = None
    delta_pass_rate: float | None = None


class MetricComparison(ContractModel):
    metric_name: str = Field(min_length=1, max_length=200)
    evaluator_version_ids: list[str] = Field(default_factory=list)
    comparable: bool
    reason: str | None = None
    points: list[ComparisonMetricPoint] = Field(default_factory=list)


class GroupComparison(ContractModel):
    group_by: Literal["category", "difficulty", "tag"]
    group_value: str = Field(min_length=1, max_length=200)
    metric_name: str = Field(min_length=1, max_length=200)
    evaluator_version_ids: list[str] = Field(default_factory=list)
    comparable: bool
    reason: str | None = None
    points: list[ComparisonMetricPoint] = Field(default_factory=list)


class CaseComparisonRun(ContractModel):
    run_id: str = Field(min_length=1, max_length=128)
    execution_status: ExecutionStatus
    output: JsonValue = None
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)
    error_type: str | None = None
    error_message: str | None = None
    failed: bool
    scores: list[Score] = Field(default_factory=list)


class FirstErrorAttribution(ContractModel):
    category: Literal[
        "tool_selection",
        "tool_arguments",
        "tool_execution",
        "retrieval",
        "final_answer",
        "format",
        "timeout",
        "cost_or_latency",
        "indeterminate",
    ]
    reason: str = Field(min_length=1)
    baseline_trace_id: str | None = None
    candidate_trace_id: str | None = None
    baseline_span_id: str | None = None
    candidate_span_id: str | None = None
    evidence: list[JsonObject] = Field(default_factory=list)


class CaseComparison(ContractModel):
    case_id: str = Field(min_length=1, max_length=128)
    metadata: JsonObject = Field(default_factory=dict)
    critical: bool = False
    runs: list[CaseComparisonRun] = Field(default_factory=list)
    first_error: FirstErrorAttribution | None = None


class CaseComparisonChange(ContractModel):
    case_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    baseline_run_id: str = Field(min_length=1, max_length=128)
    failed_metrics: list[str] = Field(default_factory=list)
    critical: bool = False
    first_error: FirstErrorAttribution | None = None


class ComparisonEvidenceGap(ContractModel):
    run_id: str = Field(min_length=1, max_length=128)
    metric_name: str = Field(min_length=1, max_length=200)
    status: Literal["missing", "error"]
    case_ids: list[str] = Field(default_factory=list)


class CriticalTaskImpact(ContractModel):
    candidate_run_id: str = Field(min_length=1, max_length=128)
    critical_case_count: int = Field(default=0, ge=0)
    baseline_failed_count: int = Field(default=0, ge=0)
    candidate_failed_count: int = Field(default=0, ge=0)
    newly_regressed_case_ids: list[str] = Field(default_factory=list)
    recovered_case_ids: list[str] = Field(default_factory=list)


class EvaluationComparison(ContractModel):
    dataset_version_id: str = Field(min_length=1, max_length=128)
    baseline_run_id: str = Field(min_length=1, max_length=128)
    runs: list[ComparisonRun] = Field(min_length=2)
    metric_comparisons: list[MetricComparison] = Field(default_factory=list)
    group_comparisons: list[GroupComparison] = Field(default_factory=list)
    case_comparisons: list[CaseComparison] = Field(default_factory=list)
    new_failures: list[CaseComparisonChange] = Field(default_factory=list)
    recovered_cases: list[CaseComparisonChange] = Field(default_factory=list)
    missing_evidence: list[ComparisonEvidenceGap] = Field(default_factory=list)
    critical_task_impact: list[CriticalTaskImpact] = Field(default_factory=list)
    generated_at: datetime


class RegressionGateRule(ContractModel):
    metric_name: str = Field(min_length=1, max_length=200)
    evaluator_version_id: str | None = Field(default=None, min_length=1, max_length=128)
    aggregation: Literal["average", "pass_rate"] = "pass_rate"
    minimum: float | None = None
    maximum: float | None = None
    require_all_passed: bool = False
    operator: GatePolicyOperator | None = None
    threshold: float | None = None
    severity: GatePolicySeverity = GatePolicySeverity.BLOCK
    critical_task_ids: list[str] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def validate_condition(self) -> RegressionGateRule:
        if self.operator is not None:
            if self.threshold is None:
                raise ValueError("operator-based gate rules require threshold")
            if self.minimum is not None or self.maximum is not None:
                raise ValueError("operator-based gate rules cannot use minimum or maximum")
            if len(self.critical_task_ids) != len(set(self.critical_task_ids)):
                raise ValueError("critical_task_ids must be unique")
            return self
        if (
            self.minimum is None
            and self.maximum is None
            and not self.require_all_passed
        ):
            raise ValueError(
                "a regression gate rule requires minimum, maximum or require_all_passed"
            )
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("minimum cannot exceed maximum")
        if len(self.critical_task_ids) != len(set(self.critical_task_ids)):
            raise ValueError("critical_task_ids must be unique")
        return self


class GatePolicyRule(ContractModel):
    metric: str = Field(min_length=1, max_length=200)
    operator: GatePolicyOperator
    threshold: float
    severity: GatePolicySeverity
    scope: GatePolicyScope = GatePolicyScope.ALL


class GatePolicy(ContractModel):
    version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    gates: list[GatePolicyRule] = Field(min_length=1, max_length=50)
    critical_tasks: list[str] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def validate_critical_tasks(self) -> GatePolicy:
        if len(self.critical_tasks) != len(set(self.critical_tasks)):
            raise ValueError("critical_tasks must be unique")
        has_critical_rule = any(
            rule.scope is GatePolicyScope.CRITICAL for rule in self.gates
        )
        if has_critical_rule and not self.critical_tasks:
            raise ValueError("critical scope requires at least one critical task")
        return self


class RegressionGateRequest(ContractModel):
    rules: list[RegressionGateRule] | None = Field(default=None, min_length=1, max_length=50)
    policy_yaml: str | None = Field(default=None, min_length=1, max_length=200_000)

    @model_validator(mode="after")
    def validate_input(self) -> RegressionGateRequest:
        if (self.rules is None) == (self.policy_yaml is None):
            raise ValueError("provide exactly one of rules or policy_yaml")
        return self


class ComparisonArtifactRequest(ComparisonRequest):
    """Request for a portable baseline/candidate artifact."""

    gate: RegressionGateRequest | None = None


class RegressionGateRuleResult(ContractModel):
    rule: RegressionGateRule
    status: RegressionGateStatus
    actual_value: float | None = None
    valid_count: int = Field(default=0, ge=0)
    missing_count: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    failed_case_ids: list[str] = Field(default_factory=list)
    reason: str | None = None


class RegressionGateResult(ContractModel):
    run_id: str = Field(min_length=1, max_length=128)
    run_status: RunStatus
    status: RegressionGateStatus
    rules: list[RegressionGateRuleResult] = Field(default_factory=list)
    policy_version: str | None = None
    generated_at: datetime


class ComparisonArtifact(ContractModel):
    schema_version: Literal[1] = 1
    artifact_type: Literal["agent-eval.comparison"] = "agent-eval.comparison"
    generated_at: datetime
    baseline_run_id: str = Field(min_length=1, max_length=128)
    candidate_run_id: str = Field(min_length=1, max_length=128)
    comparison: EvaluationComparison
    gate: RegressionGateResult | None = None


class HealthResponse(ContractModel):
    status: Literal["ok"]
    environment: Literal["development", "test", "production"]


class AccessCheckResponse(ContractModel):
    project_id: str = Field(min_length=1, max_length=128)
    principal_type: Literal["browser", "agent", "ci"]


class ProjectApiKeyCreateRequest(ContractModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectApiKey(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    key_prefix: str = Field(min_length=1, max_length=16)
    active: bool
    created_at: datetime
    last_used_at: datetime | None = None


class ProjectApiKeyCreated(ProjectApiKey):
    key: str = Field(min_length=1)


class AgentReleaseRegistrationRequest(ContractModel):
    """Register immutable release identity without requiring an HTTP endpoint."""

    label: str = Field(min_length=1, max_length=100)
    agent_type: AgentType
    release_identity: str = Field(min_length=1, max_length=200)
    source_revision: str | None = Field(default=None, min_length=1, max_length=200)
    metadata: JsonObject = Field(default_factory=dict)


class AgentReleaseResponse(ContractModel):
    id: str
    project_id: str
    version: int
    label: str
    agent_type: AgentType
    release_identity: str
    source_revision: str | None
    metadata: JsonObject = Field(default_factory=dict)
    enabled: bool
    created_at: datetime


class LegacyHttpAgentMigrationRequest(ContractModel):
    """Convert a historical per-Case HTTP release into a supported runtime mode."""

    legacy_agent_version_id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    target_mode: Literal["remote_upload", "remote_trigger"]
    trigger_url: HttpUrl | None = None

    @model_validator(mode="after")
    def validate_target(self) -> LegacyHttpAgentMigrationRequest:
        if self.target_mode == "remote_trigger" and self.trigger_url is None:
            raise ValueError("trigger_url is required for remote_trigger migration")
        if self.target_mode == "remote_upload" and self.trigger_url is not None:
            raise ValueError("trigger_url is only allowed for remote_trigger migration")
        if self.trigger_url is not None and (
            self.trigger_url.username
            or self.trigger_url.password
            or self.trigger_url.query
            or self.trigger_url.fragment
        ):
            raise ValueError("trigger_url must not contain credentials, query, or fragment")
        return self


class LegacyHttpAgentMigrationResponse(ContractModel):
    release: AgentReleaseResponse
    trigger: RemoteTriggerCreated | None = None


class EvaluatorConnection(ContractModel):
    """A user-managed external LLM Judge endpoint reference."""

    id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    endpoint: HttpUrl
    auth_ref: str | None = Field(default=None, min_length=1, max_length=200)
    timeout_seconds: float = Field(default=60.0, gt=0.0, le=300.0)
    enabled: bool = True
    created_at: datetime


class EvaluatorConnectionCreateRequest(ContractModel):
    name: str = Field(min_length=1, max_length=200)
    endpoint: HttpUrl
    auth_ref: str | None = Field(default=None, min_length=1, max_length=200)
    timeout_seconds: float = Field(default=60.0, gt=0.0, le=300.0)


class ProviderConnection(ContractModel):
    """Public metadata for an encrypted platform-managed model connection."""

    id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    provider: ProviderKind
    base_url: HttpUrl
    model: str = Field(min_length=1, max_length=200)
    default_parameters: JsonObject = Field(default_factory=dict)
    credential_mask: str = Field(min_length=1, max_length=64)
    credential_key_id: str = Field(min_length=1, max_length=128)
    status: ProviderConnectionStatus
    enabled: bool
    created_at: datetime
    updated_at: datetime
    tested_at: datetime | None


class ProviderConnectionTestRequest(ContractModel):
    provider: ProviderKind = ProviderKind.OPENAI_COMPATIBLE
    base_url: HttpUrl
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = Field(min_length=1, max_length=8192)
    default_parameters: JsonObject = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)

    @model_validator(mode="after")
    def validate_provider_configuration(self) -> Self:
        from urllib.parse import urlsplit

        parsed = urlsplit(str(self.base_url))
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "base_url must not contain credentials, query, or fragment"
            )
        reserved = {"model", "messages", "stream", "api_key", "base_url", "timeout"}
        normalized = {
            "".join(character.lower() for character in key if character.isalnum())
            for key in self.default_parameters
        }
        if normalized & {
            "".join(character for character in key if character.isalnum())
            for key in reserved
        }:
            raise ValueError("default_parameters contains a reserved field")
        return self


class ProviderConnectionCreateRequest(ProviderConnectionTestRequest):
    name: str = Field(min_length=1, max_length=200)


class ProviderConnectionRotateRequest(ContractModel):
    """Replacement credential for an existing, already validated connection."""

    api_key: SecretStr = Field(min_length=1, max_length=8192)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)


class ProviderConnectionTestResponse(ContractModel):
    provider: ProviderKind
    configured_model: str = Field(min_length=1, max_length=200)
    response_model: str = Field(min_length=1, max_length=200)
    upstream_request_id: str = Field(min_length=1, max_length=500)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(gt=0)
    challenge_verified: Literal[True] = True


class ProviderConnectionExport(ContractModel):
    schema_version: Literal[1] = 1
    generated_at: datetime
    connections: list[ProviderConnection]


class DatasetCreateRequest(ContractModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    cases: list[DatasetCase] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_tags(self) -> DatasetCreateRequest:
        if any(not tag for tag in self.tags) or len(set(self.tags)) != len(self.tags):
            raise ValueError("tags must be non-empty and unique")
        return self


class DatasetUpdateRequest(ContractModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    tags: list[str] | None = None

    @model_validator(mode="after")
    def validate_tags(self) -> DatasetUpdateRequest:
        if self.tags is not None and (
            any(not tag for tag in self.tags) or len(set(self.tags)) != len(self.tags)
        ):
            raise ValueError("tags must be non-empty and unique")
        return self


class DatasetVersionCreateRequest(ContractModel):
    cases: list[DatasetCase] = Field(default_factory=list)
    metadata: JsonObject | None = None


class DatasetCaseCreateRequest(DatasetCase):
    pass


class DatasetResponse(Dataset):
    pass


class DatasetImportRequest(ContractModel):
    format: Literal["csv", "json", "jsonl"]
    content_base64: str = Field(min_length=1)
    encoding: str = Field(default="utf-8", min_length=1, max_length=50)
    field_mapping: dict[str, str] = Field(default_factory=dict)


class DatasetImportCommitRequest(DatasetImportRequest):
    metadata: JsonObject | None = None
    allow_partial: bool = False


class DatasetImportIssueResponse(ContractModel):
    line: int = Field(ge=0)
    reason: str


class DatasetImportPreviewResponse(ContractModel):
    cases: list[DatasetCase] = Field(default_factory=list)
    issues: list[DatasetImportIssueResponse] = Field(default_factory=list)


class DatasetImportCommitResponse(ContractModel):
    dataset_version: DatasetVersion
    issues: list[DatasetImportIssueResponse] = Field(default_factory=list)
