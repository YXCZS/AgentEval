"""Strict public models for the Agent Eval SDK."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JsonObject = dict[str, Any]


class SdkModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Dataset(SdkModel):
    id: str
    project_id: str
    name: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    current_version_id: str | None = None
    created_at: datetime
    updated_at: datetime


class DatasetCase(SdkModel):
    id: str
    input: Any
    variables: JsonObject = Field(default_factory=dict)
    expected_output: Any = None
    output_schema: JsonObject | None = None
    criteria: list[str] = Field(default_factory=list)
    expected_tools: list[JsonObject] = Field(default_factory=list)
    expected_state: JsonObject | None = None
    retrieval_context: list[JsonObject] = Field(default_factory=list)
    messages: list[JsonObject] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)
    source_trace_id: str | None = None
    source_span_ids: list[str] = Field(default_factory=list)
    source_mapping: JsonObject = Field(default_factory=dict)


class DatasetVersion(SdkModel):
    id: str
    dataset_id: str
    version: int
    cases: list[DatasetCase] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)
    created_at: datetime


class AgentRelease(SdkModel):
    id: str
    project_id: str
    version: int
    label: str
    agent_type: Literal["rag", "tool", "custom"]
    release_identity: str
    source_revision: str | None
    metadata: JsonObject = Field(default_factory=dict)
    enabled: bool
    created_at: datetime


class Experiment(SdkModel):
    id: str
    name: str
    agent_version_id: str
    dataset_version_id: str
    evaluator_version_ids: list[str]
    execution_mode: Literal["sdk_task", "otel", "remote_upload", "remote_trigger"]
    evidence_policy: Literal[
        "trace_required",
        "llm_required",
        "tool_trajectory_required",
        "rag_trajectory_required",
    ]
    status: Literal["queued", "running", "completed", "partial", "failed", "cancelled"]
    total_cases: int
    completed_cases: int
    failed_cases: int
    baseline_run_id: str | None
    execution_options: JsonObject
    configuration_snapshot: JsonObject
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ExperimentItem(SdkModel):
    id: str
    experiment_id: str
    case_id: str
    repetition: int
    attempt: int
    external_run_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    output: Any = None
    usage: JsonObject = Field(default_factory=dict)
    runtime_metadata: JsonObject = Field(default_factory=dict)
    error_type: str | None = None
    error_message: str | None = None
    trace_id: str | None = None
    evidence_status: Literal["pending", "complete", "incomplete", "not_required"] = "pending"
    evidence_reasons: list[str] = Field(default_factory=list)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ManifestItem(SdkModel):
    case: DatasetCase
    attempts: list[ExperimentItem] = Field(default_factory=list)


class ManifestPage(SdkModel):
    experiment_id: str
    dataset_version_id: str
    items: list[ManifestItem]
    total: int
    offset: int
    limit: int
    next_offset: int | None


class DatasetSelection(SdkModel):
    dataset: Dataset
    version: DatasetVersion
