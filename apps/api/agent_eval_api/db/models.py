"""Relational persistence model.

Frequently filtered identifiers are relational columns.  Agent-specific and
provider-specific payloads remain JSON so the schema can evolve without
flattening every new agent framework into a migration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JsonType = JSON().with_variant(JSONB, "postgresql")


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class ProjectRecord(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    api_keys: Mapped[list[ApiKeyRecord]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    agents: Mapped[list[AgentRecord]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    agent_releases: Mapped[list[AgentVersionRecord]] = relationship(
        back_populates="project"
    )
    datasets: Mapped[list[DatasetRecord]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    evaluators: Mapped[list[EvaluatorVersionRecord]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    evaluator_connections: Mapped[list[EvaluatorConnectionRecord]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    provider_connections: Mapped[list[ProviderConnectionRecord]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    traces: Mapped[list[TraceRecord]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    users: Mapped[list[UserRecord]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class UserRecord(Base):
    """A human user. Each user owns exactly one project, which is their private data space."""

    __tablename__ = "users"
    __table_args__ = (Index("ix_users_project", "project_id"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="member", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[ProjectRecord] = relationship(back_populates="users")


class ApiKeyRecord(Base):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("project_id", "key_hash", name="uq_api_keys_project_hash"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[ProjectRecord] = relationship(back_populates="api_keys")


class AgentRecord(Base):
    __tablename__ = "agents"
    __table_args__ = (Index("ix_agents_project_type", "project_id", "agent_type"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    project: Mapped[ProjectRecord] = relationship(back_populates="agents")
    versions: Mapped[list[AgentVersionRecord]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )


class AgentVersionRecord(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
        Index("ix_agent_versions_agent_enabled", "agent_id", "enabled"),
        Index("ix_agent_versions_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    release_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    source_revision: Mapped[str | None] = mapped_column(String(200))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, default=dict, nullable=False
    )
    endpoint_config: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    agent: Mapped[AgentRecord | None] = relationship(back_populates="versions")
    project: Mapped[ProjectRecord] = relationship(back_populates="agent_releases")
    runs: Mapped[list[EvaluationRunRecord]] = relationship(back_populates="agent_version")


class DatasetRecord(Base):
    __tablename__ = "datasets"
    __table_args__ = (Index("ix_datasets_project_name", "project_id", "name"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(ForeignKey("dataset_versions.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    project: Mapped[ProjectRecord] = relationship(back_populates="datasets")
    versions: Mapped[list[DatasetVersionRecord]] = relationship(
        back_populates="dataset",
        foreign_keys="DatasetVersionRecord.dataset_id",
        cascade="all, delete-orphan",
    )
    current_version: Mapped[DatasetVersionRecord | None] = relationship(
        foreign_keys=[current_version_id], post_update=True
    )
    remote_trigger: Mapped[RemoteTriggerRecord | None] = relationship(
        back_populates="dataset", uselist=False, cascade="all, delete-orphan"
    )


class RemoteTriggerRecord(Base):
    """Dataset-scoped remote runner configuration and encrypted signing secret."""

    __tablename__ = "remote_triggers"
    __table_args__ = (
        UniqueConstraint("dataset_id", name="uq_remote_triggers_dataset"),
        Index("ix_remote_triggers_project_enabled", "project_id", "enabled"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    trigger_url: Mapped[str] = mapped_column(String(500), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    signature_header: Mapped[str] = mapped_column(
        String(100), nullable=False, default="X-Agent-Eval-Trigger-Signature"
    )
    secret_mask: Mapped[str] = mapped_column(String(64), nullable=False)
    secret_key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    secret_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    project: Mapped[ProjectRecord] = relationship()
    dataset: Mapped[DatasetRecord] = relationship(back_populates="remote_trigger")
    deliveries: Mapped[list[RemoteTriggerDeliveryRecord]] = relationship(
        back_populates="trigger", cascade="all, delete-orphan"
    )


class RemoteTriggerDeliveryRecord(Base):
    """A durable webhook delivery, separate from the remote Experiment itself."""

    __tablename__ = "remote_trigger_deliveries"
    __table_args__ = (
        UniqueConstraint("delivery_id", name="uq_remote_trigger_deliveries_delivery_id"),
        UniqueConstraint("experiment_id", name="uq_remote_trigger_deliveries_experiment"),
        Index("ix_remote_trigger_deliveries_trigger_created", "trigger_id", "created_at"),
        CheckConstraint(
            "status IN ('pending', 'delivering', 'accepted', 'rejected', 'failed')",
            name="ck_remote_trigger_delivery_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    trigger_id: Mapped[str] = mapped_column(
        ForeignKey("remote_triggers.id", ondelete="CASCADE"), nullable=False
    )
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    delivery_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    last_error_type: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(String(200))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    trigger: Mapped[RemoteTriggerRecord] = relationship(back_populates="deliveries")
    experiment: Mapped[EvaluationRunRecord] = relationship(back_populates="trigger_deliveries")


class DatasetVersionRecord(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version", name="uq_dataset_versions_dataset_version"),
        Index("ix_dataset_versions_dataset_created", "dataset_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    dataset: Mapped[DatasetRecord] = relationship(
        back_populates="versions", foreign_keys=[dataset_id]
    )
    cases: Mapped[list[DatasetCaseRecord]] = relationship(
        back_populates="dataset_version", cascade="all, delete-orphan"
    )
    runs: Mapped[list[EvaluationRunRecord]] = relationship(back_populates="dataset_version")


class DatasetCaseRecord(Base):
    __tablename__ = "dataset_cases"
    __table_args__ = (
        UniqueConstraint("dataset_version_id", "case_key", name="uq_dataset_cases_version_key"),
        Index("ix_dataset_cases_version", "dataset_version_id"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    dataset_version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), nullable=False
    )
    case_key: Mapped[str] = mapped_column(String(128), nullable=False)
    input_json: Mapped[Any] = mapped_column("input", JsonType, nullable=False)
    variables: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    expected_output: Mapped[Any | None] = mapped_column(JsonType)
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    criteria: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    expected_tools: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType, default=list, nullable=False
    )
    expected_state: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    retrieval_context: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType, default=list, nullable=False
    )
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, default=dict, nullable=False
    )
    source_trace_id: Mapped[str | None] = mapped_column(String(128))
    source_span_ids: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    source_mapping: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)

    dataset_version: Mapped[DatasetVersionRecord] = relationship(back_populates="cases")
    executions: Mapped[list[CaseExecutionRecord]] = relationship(back_populates="dataset_case")
    experiment_items: Mapped[list[ExperimentItemAttemptRecord]] = relationship(
        back_populates="dataset_case"
    )


class EvaluatorVersionRecord(Base):
    __tablename__ = "evaluator_versions"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "name",
            "version",
            name="uq_evaluator_versions_project_name_version",
        ),
        Index("ix_evaluator_versions_project_enabled", "project_id", "enabled"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    evaluator_type: Mapped[str] = mapped_column(String(32), nullable=False)
    requires: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    supported_agent_types: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    score_min: Mapped[float | None] = mapped_column(Float)
    score_max: Mapped[float | None] = mapped_column(Float)
    direction: Mapped[str] = mapped_column(String(32), nullable=False)
    default_threshold: Mapped[float | None] = mapped_column(Float)
    rubric: Mapped[str | None] = mapped_column(Text)
    evaluator_connection_id: Mapped[str | None] = mapped_column(
        ForeignKey("evaluator_connections.id")
    )
    provider_connection_id: Mapped[str | None] = mapped_column(
        ForeignKey("provider_connections.id")
    )
    judge_model: Mapped[str | None] = mapped_column(String(200))
    prompt_template: Mapped[str | None] = mapped_column(Text)
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    sampling_parameters: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    config: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped[ProjectRecord] = relationship(back_populates="evaluators")
    evaluator_connection: Mapped[EvaluatorConnectionRecord | None] = relationship()
    provider_connection: Mapped[ProviderConnectionRecord | None] = relationship()


class EvaluatorConnectionRecord(Base):
    __tablename__ = "evaluator_connections"
    __table_args__ = (Index("ix_evaluator_connections_project", "project_id"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    auth_ref: Mapped[str | None] = mapped_column(String(200))
    timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=60.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped[ProjectRecord] = relationship(back_populates="evaluator_connections")


class ProviderConnectionRecord(Base):
    """Encrypted model credentials used only by platform-managed evaluators."""

    __tablename__ = "provider_connections"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "name", name="uq_provider_connections_project_name"
        ),
        Index("ix_provider_connections_project_status", "project_id", "status"),
        CheckConstraint(
            "status IN ('pending_validation', 'active', 'error', 'disabled')",
            name="ck_provider_connections_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    default_parameters: Mapped[dict[str, Any]] = mapped_column(
        JsonType, default=dict, nullable=False
    )
    credential_mask: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    credential_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    credential_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending_validation"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[ProjectRecord] = relationship(back_populates="provider_connections")


class EvaluationRunRecord(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        Index("ix_evaluation_runs_project_created", "project_id", "created_at"),
        Index("ix_evaluation_runs_status", "status"),
        CheckConstraint(
            "execution_mode IN ('sdk_task', 'otel', 'remote_upload', 'remote_trigger')",
            name="ck_evaluation_runs_execution_mode",
        ),
        CheckConstraint(
            "evidence_policy IN ('trace_required', 'llm_required', "
            "'tool_trajectory_required', 'rag_trajectory_required')",
            name="ck_evaluation_runs_evidence_policy",
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="Experiment")
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"), nullable=False)
    dataset_version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.id"), nullable=False
    )
    baseline_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="SET NULL")
    )
    execution_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="sdk_task"
    )
    evidence_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="trace_required"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    configuration_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JsonType, default=dict, nullable=False
    )
    total_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    agent_version: Mapped[AgentVersionRecord] = relationship(back_populates="runs")
    dataset_version: Mapped[DatasetVersionRecord] = relationship(back_populates="runs")
    executions: Mapped[list[CaseExecutionRecord]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    items: Mapped[list[ExperimentItemAttemptRecord]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )
    traces: Mapped[list[TraceRecord]] = relationship(back_populates="run")
    scores: Mapped[list[ScoreRecord]] = relationship(back_populates="run")
    trigger_deliveries: Mapped[list[RemoteTriggerDeliveryRecord]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )


class CaseExecutionRecord(Base):
    __tablename__ = "case_executions"
    __table_args__ = (
        UniqueConstraint("run_id", "case_id", name="uq_case_executions_run_case"),
        Index("ix_case_executions_run_status", "run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[str] = mapped_column(ForeignKey("dataset_cases.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output: Mapped[Any | None] = mapped_column(JsonType)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list, nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(ForeignKey("traces.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[EvaluationRunRecord] = relationship(back_populates="executions")
    dataset_case: Mapped[DatasetCaseRecord] = relationship(back_populates="executions")
    trace: Mapped[TraceRecord | None] = relationship(foreign_keys=[trace_id])


class ExperimentItemAttemptRecord(Base):
    """Attempt-level evidence produced outside the platform control plane."""

    __tablename__ = "experiment_item_attempts"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "case_id",
            "repetition",
            "attempt",
            name="uq_experiment_item_attempt_position",
        ),
        UniqueConstraint(
            "experiment_id",
            "external_run_id",
            name="uq_experiment_item_external_run",
        ),
        Index("ix_experiment_item_status", "experiment_id", "status"),
        CheckConstraint("repetition >= 1", name="ck_experiment_item_repetition_positive"),
        CheckConstraint("attempt >= 1", name="ck_experiment_item_attempt_positive"),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_experiment_item_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[str] = mapped_column(ForeignKey("dataset_cases.id"), nullable=False)
    repetition: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    external_run_id: Mapped[str] = mapped_column(String(200), nullable=False)
    result_hash: Mapped[str | None] = mapped_column(String(64))
    source_trace_id: Mapped[str | None] = mapped_column(String(128))
    evidence_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    evidence_reasons: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    output: Mapped[Any | None] = mapped_column(JsonType)
    usage: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    runtime_metadata: Mapped[dict[str, Any]] = mapped_column(
        JsonType, default=dict, nullable=False
    )
    error_type: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(ForeignKey("traces.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    experiment: Mapped[EvaluationRunRecord] = relationship(back_populates="items")
    dataset_case: Mapped[DatasetCaseRecord] = relationship(back_populates="experiment_items")
    trace: Mapped[TraceRecord | None] = relationship(foreign_keys=[trace_id])


class TraceRecord(Base):
    __tablename__ = "traces"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "source", "trace_id", name="uq_traces_project_source_trace"
        ),
        Index("ix_traces_project_created", "project_id", "created_at"),
        Index("ix_traces_run_case", "run_id", "case_id"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # Public ID supplied by the external Agent. ``id`` stays internal because
    # existing Score/Execution/Span foreign keys point at it.
    trace_id: Mapped[str | None] = mapped_column(String(128))
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="SET NULL")
    )
    case_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="platform")
    extensions: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped[ProjectRecord] = relationship(back_populates="traces")
    run: Mapped[EvaluationRunRecord | None] = relationship(back_populates="traces")
    spans: Mapped[list[TraceSpanRecord]] = relationship(
        back_populates="trace", cascade="all, delete-orphan"
    )
    scores: Mapped[list[ScoreRecord]] = relationship(back_populates="trace")


@event.listens_for(TraceRecord, "before_insert")
def populate_legacy_trace_id(_: Any, __: Any, target: TraceRecord) -> None:
    """Keep direct ORM inserts compatible with pre-scoped Trace records."""

    if target.trace_id is None:
        target.trace_id = target.id


class TraceSpanRecord(Base):
    __tablename__ = "trace_spans"
    __table_args__ = (
        UniqueConstraint("trace_id", "span_id", name="uq_trace_spans_trace_span"),
        Index("ix_trace_spans_trace_parent", "trace_id", "parent_span_id"),
        Index("ix_trace_spans_trace_started", "trace_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    trace_id: Mapped[str] = mapped_column(
        ForeignKey("traces.id", ondelete="CASCADE"), nullable=False
    )
    span_id: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input: Mapped[Any | None] = mapped_column(JsonType)
    output: Mapped[Any | None] = mapped_column(JsonType)
    error: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    usage: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    cost: Mapped[Any | None] = mapped_column(JsonType)
    attributes: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    extensions: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)

    trace: Mapped[TraceRecord] = relationship(back_populates="spans")


class ScoreRecord(Base):
    __tablename__ = "scores"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "case_id",
            "repetition",
            "metric_name",
            "evaluator_version_id",
            "source",
            name="uq_scores_case_metric_source",
        ),
        Index("ix_scores_run_metric", "run_id", "metric_name"),
        CheckConstraint("repetition >= 1", name="ck_scores_repetition_positive"),
        CheckConstraint("attempt IS NULL OR attempt >= 1", name="ck_scores_attempt_positive"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE")
    )
    case_id: Mapped[str | None] = mapped_column(String(128))
    experiment_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("experiment_item_attempts.id", ondelete="SET NULL")
    )
    repetition: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempt: Mapped[int | None] = mapped_column(Integer)
    metric_name: Mapped[str] = mapped_column(String(200), nullable=False)
    evaluator_version_id: Mapped[str] = mapped_column(
        ForeignKey("evaluator_versions.id"), nullable=False
    )
    trace_id: Mapped[str | None] = mapped_column(ForeignKey("traces.id"))
    span_id: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="automated")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    label: Mapped[str | None] = mapped_column(String(100))
    passed: Mapped[bool | None] = mapped_column(Boolean)
    explanation: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list, nullable=False)
    rubric: Mapped[str | None] = mapped_column(Text)
    judge_model: Mapped[str | None] = mapped_column(String(200))
    provenance: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    threshold: Mapped[float | None] = mapped_column(Float)
    direction: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_response: Mapped[Any | None] = mapped_column(JsonType)
    raw_result: Mapped[Any | None] = mapped_column(JsonType)

    run: Mapped[EvaluationRunRecord | None] = relationship(back_populates="scores")
    trace: Mapped[TraceRecord | None] = relationship(back_populates="scores")


class AnnotationQueueRecord(Base):
    __tablename__ = "annotation_queues"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_annotation_queues_project_name"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    evaluator_version_id: Mapped[str] = mapped_column(
        ForeignKey("evaluator_versions.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    items: Mapped[list[AnnotationQueueItemRecord]] = relationship(
        back_populates="queue", cascade="all, delete-orphan"
    )


class AnnotationQueueItemRecord(Base):
    __tablename__ = "annotation_queue_items"
    __table_args__ = (
        UniqueConstraint("queue_id", "run_id", "case_id", name="uq_annotation_item_case"),
        Index("ix_annotation_items_queue_status", "queue_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    queue_id: Mapped[str] = mapped_column(
        ForeignKey("annotation_queues.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(ForeignKey("traces.id"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    queue: Mapped[AnnotationQueueRecord] = relationship(back_populates="items")


class HumanScoreAuditRecord(Base):
    __tablename__ = "human_score_audits"
    __table_args__ = (Index("ix_human_score_audits_score_created", "score_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    score_id: Mapped[str] = mapped_column(
        ForeignKey("scores.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(200), nullable=False)
    previous_value: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    new_value: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class AggregateMetricRecord(Base):
    __tablename__ = "aggregate_metrics"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "metric_name",
            "evaluator_version_id",
            name="uq_aggregate_metrics_run_metric_evaluator",
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    metric_name: Mapped[str] = mapped_column(String(200), nullable=False)
    evaluator_version_id: Mapped[str] = mapped_column(
        ForeignKey("evaluator_versions.id"), nullable=False
    )
    valid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    average: Mapped[float | None] = mapped_column(Float)
    pass_rate: Mapped[float | None] = mapped_column(Float)
    aggregation: Mapped[str] = mapped_column(String(32), nullable=False)
    threshold: Mapped[float | None] = mapped_column(Float)
    direction: Mapped[str] = mapped_column(String(32), nullable=False)


@event.listens_for(AgentVersionRecord, "before_update")
@event.listens_for(DatasetVersionRecord, "before_update")
@event.listens_for(EvaluatorVersionRecord, "before_update")
def reject_version_mutation(_: Any, __: Any, target: Any) -> None:
    """Version rows are snapshots; create a new row instead of mutating history."""

    state = inspect(target)
    changed = {
        attribute.key
        for attribute in state.mapper.column_attrs
        if state.attrs[attribute.key].history.has_changes()
    }
    if isinstance(target, (AgentVersionRecord, EvaluatorVersionRecord)) and changed <= {"enabled"}:
        return
    raise ValueError("versioned records are immutable")


@event.listens_for(ExperimentItemAttemptRecord, "before_update")
def reject_terminal_experiment_item_mutation(
    _: Any, __: Any, target: ExperimentItemAttemptRecord
) -> None:
    """Once an attempt is terminal, its output and provenance are evidence."""

    status_history = inspect(target).attrs.status.history
    previous_status = status_history.deleted[0] if status_history.deleted else target.status
    state = inspect(target)
    changed = {
        attribute.key
        for attribute in state.mapper.column_attrs
        if state.attrs[attribute.key].history.has_changes()
    }
    derived_fields = {"trace_id", "evidence_status", "evidence_reasons"}
    if previous_status in {"completed", "failed", "cancelled"} and not changed <= derived_fields:
        raise ValueError("terminal experiment item attempts are immutable")


@event.listens_for(EvaluationRunRecord, "before_update")
def reject_experiment_definition_mutation(_: Any, __: Any, target: EvaluationRunRecord) -> None:
    """Execution state changes, but the submitted Experiment definition does not."""

    immutable_fields = {
        "project_id",
        "name",
        "agent_version_id",
        "dataset_version_id",
        "baseline_run_id",
        "execution_mode",
        "evidence_policy",
        "configuration_snapshot",
        "created_at",
    }
    state = inspect(target)
    changed = {
        attribute.key
        for attribute in state.mapper.column_attrs
        if state.attrs[attribute.key].history.has_changes()
    }
    if changed & immutable_fields:
        raise ValueError("experiment definitions are immutable")
