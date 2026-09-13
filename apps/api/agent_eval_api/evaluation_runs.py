"""Creation and inspection of reproducible, project-scoped evaluation runs."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent_eval_api.auth import AuthContext, get_db, require_project_access
from agent_eval_api.contracts import (
    CaseExecution,
    DatasetCase,
    EvaluationRun,
    EvaluationRunCreateRequest,
    EvaluationRunDetail,
    EvidencePolicy,
    ExecutionStatus,
    ExpectedToolCall,
    ExperimentExecutionMode,
    ExperimentItemAttempt,
    ExperimentItemCancelRequest,
    ExperimentItemCompleteRequest,
    ExperimentItemFailRequest,
    ExperimentItemStartRequest,
    ExperimentManifestItem,
    ExperimentManifestPage,
    ExperimentPage,
    RunStatus,
    ScoreStatus,
)
from agent_eval_api.db import (
    AgentRecord,
    AgentVersionRecord,
    CaseExecutionRecord,
    DatasetCaseRecord,
    DatasetRecord,
    DatasetVersionRecord,
    EvaluationRunRecord,
    EvaluatorConnectionRecord,
    EvaluatorVersionRecord,
    ExperimentItemAttemptRecord,
    ProjectRecord,
    ProviderConnectionRecord,
    RemoteTriggerDeliveryRecord,
    RemoteTriggerRecord,
    ScoreRecord,
    TraceRecord,
    new_id,
)
from agent_eval_api.remote_triggers import (
    RemoteTriggerDeliveryError,
    build_experiment_trigger_delivery,
    deliver_experiment_trigger,
)
from agent_eval_api.settings import Settings, get_settings

router = APIRouter(prefix="/projects/{project_id}/runs", tags=["evaluation-runs"])
experiments_router = APIRouter(
    prefix="/projects/{project_id}/experiments",
    tags=["experiments"],
)

_RUNTIME_REQUIREMENTS = {
    "output",
    "execution_output",
    "tool_calls",
    "usage",
    "latency",
    "cost",
    "trace",
    "trace.spans",
}
_OPTIONAL_CASE_REQUIREMENTS = {
    "expected_output",
    "output_schema",
    "criteria",
    "expected_tools",
    "expected_state",
    "retrieval_context",
    "messages",
}


def get_project(db: Session, project_id: str) -> ProjectRecord:
    project = db.get(ProjectRecord, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return project


def get_project_agent_version(
    db: Session, project_id: str, agent_version_id: str
) -> tuple[AgentRecord | None, AgentVersionRecord]:
    version = db.scalar(
        select(AgentVersionRecord)
        .where(
            AgentVersionRecord.project_id == project_id,
            AgentVersionRecord.id == agent_version_id,
        )
    )
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent version not found")
    return version.agent, version


def get_project_dataset_version(
    db: Session, project_id: str, dataset_version_id: str
) -> DatasetVersionRecord:
    version = db.scalar(
        select(DatasetVersionRecord)
        .join(DatasetRecord, DatasetVersionRecord.dataset_id == DatasetRecord.id)
        .where(
            DatasetRecord.project_id == project_id,
            DatasetVersionRecord.id == dataset_version_id,
        )
    )
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="dataset version not found"
        )
    return version


def get_project_evaluators(
    db: Session, project_id: str, evaluator_ids: list[str]
) -> list[EvaluatorVersionRecord]:
    records = db.scalars(
        select(EvaluatorVersionRecord).where(
            EvaluatorVersionRecord.project_id == project_id,
            EvaluatorVersionRecord.id.in_(evaluator_ids),
        )
    ).all()
    by_id = {record.id: record for record in records}
    missing = [evaluator_id for evaluator_id in evaluator_ids if evaluator_id not in by_id]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "evaluator not found", "evaluator_version_ids": missing},
        )
    return [by_id[evaluator_id] for evaluator_id in evaluator_ids]


def case_has_requirement(case: DatasetCaseRecord, requirement: str) -> bool:
    if requirement in _RUNTIME_REQUIREMENTS:
        return True
    if requirement == "input":
        return case.input_json is not None
    if requirement == "expected_tools":
        # An empty list is a valid expectation: the agent should call no tools.
        return case.expected_tools is not None
    if requirement in _OPTIONAL_CASE_REQUIREMENTS:
        return bool(getattr(case, requirement))
    if requirement == "variables" or requirement == "metadata":
        return True
    if requirement.startswith("metadata."):
        key = requirement.removeprefix("metadata.")
        return bool(key) and key in case.metadata_json
    return False


def validate_evaluator_requirements(
    evaluators: Sequence[EvaluatorVersionRecord], cases: Sequence[DatasetCaseRecord]
) -> None:
    missing: list[dict[str, Any]] = []
    for evaluator in evaluators:
        for case in cases:
            unavailable = [
                requirement
                for requirement in evaluator.requires
                if not case_has_requirement(case, requirement)
            ]
            if unavailable:
                missing.append(
                    {
                        "evaluator_version_id": evaluator.id,
                        "case_id": case.case_key,
                        "fields": unavailable,
                    }
                )
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "evaluator requirements are missing from dataset cases",
                "missing": missing,
            },
        )


def agent_snapshot(agent: AgentRecord | None, version: AgentVersionRecord) -> dict[str, Any]:
    snapshot = {
        "agent_id": agent.id if agent is not None else None,
        "connection_id": agent.id if agent is not None else None,
        "name": agent.name if agent is not None else version.label,
        "agent_type": version.agent_type,
        "version_id": version.id,
        "version": version.version,
        "label": version.label,
        "release_identity": version.release_identity,
        "source_revision": version.source_revision,
        "metadata": deepcopy(version.metadata_json),
    }
    return snapshot


def case_snapshot(case: DatasetCaseRecord) -> dict[str, Any]:
    return {
        "id": case.id,
        "case_id": case.case_key,
        "input": deepcopy(case.input_json),
        "variables": deepcopy(case.variables),
        "expected_output": deepcopy(case.expected_output),
        "output_schema": deepcopy(case.output_schema),
        "criteria": deepcopy(case.criteria),
        "expected_tools": deepcopy(case.expected_tools),
        "expected_state": deepcopy(case.expected_state),
        "retrieval_context": deepcopy(case.retrieval_context),
        "messages": deepcopy(case.messages),
        "metadata": deepcopy(case.metadata_json),
        "source_trace_id": case.source_trace_id,
        "source_span_ids": deepcopy(case.source_span_ids),
        "source_mapping": deepcopy(case.source_mapping),
    }


def evaluator_snapshot(
    evaluator: EvaluatorVersionRecord,
    connection: EvaluatorConnectionRecord | None = None,
    provider: ProviderConnectionRecord | None = None,
) -> dict[str, Any]:
    return {
        "id": evaluator.id,
        "name": evaluator.name,
        "version": evaluator.version,
        "evaluator_type": evaluator.evaluator_type,
        "requires": evaluator.requires,
        "supported_agent_types": evaluator.supported_agent_types,
        "score_min": evaluator.score_min,
        "score_max": evaluator.score_max,
        "direction": evaluator.direction,
        "default_threshold": evaluator.default_threshold,
        "rubric": evaluator.rubric,
        "judge_model": evaluator.judge_model,
        "prompt_template": evaluator.prompt_template,
        "output_schema": deepcopy(evaluator.output_schema),
        "sampling_parameters": deepcopy(evaluator.sampling_parameters),
        "config": deepcopy(evaluator.config),
        "evaluator_connection": (
            {
                "id": connection.id,
                "project_id": connection.project_id,
                "name": connection.name,
                "endpoint": connection.endpoint,
                "auth_ref": connection.auth_ref,
                "timeout_seconds": connection.timeout_seconds,
                "enabled": connection.enabled,
            }
            if connection is not None
            else None
        ),
        "provider_connection": (
            {
                "id": provider.id,
                "project_id": provider.project_id,
                "name": provider.name,
                "provider": provider.provider,
                "base_url": provider.base_url,
                "model": provider.model,
                "default_parameters": deepcopy(provider.default_parameters),
                "status": provider.status,
                "enabled": provider.enabled,
            }
            if provider is not None
            else None
        ),
    }


def run_response(record: EvaluationRunRecord) -> EvaluationRun:
    evaluator_ids = [item["id"] for item in record.configuration_snapshot["evaluators"]]
    baseline_snapshot = record.configuration_snapshot.get("baseline") or {}
    return EvaluationRun(
        id=record.id,
        name=record.name,
        agent_version_id=record.agent_version_id,
        dataset_version_id=record.dataset_version_id,
        evaluator_version_ids=evaluator_ids,
        execution_mode=ExperimentExecutionMode(record.execution_mode),
        evidence_policy=EvidencePolicy(record.evidence_policy),
        status=RunStatus(record.status),
        total_cases=record.total_cases,
        completed_cases=record.completed_cases,
        failed_cases=record.failed_cases,
        baseline_run_id=baseline_snapshot.get("run_id"),
        execution_options=record.configuration_snapshot.get("execution_options", {}),
        configuration_snapshot=record.configuration_snapshot,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )


def execution_response(record: CaseExecutionRecord) -> CaseExecution:
    return CaseExecution(
        id=record.id,
        run_id=record.run_id,
        case_id=record.dataset_case.case_key,
        status=ExecutionStatus(record.status),
        attempt=record.attempt,
        output=record.output,
        tool_calls=[ExpectedToolCall.model_validate(item) for item in record.tool_calls],
        usage=record.usage,
        error_type=record.error_type,
        error_message=record.error_message,
        trace_id=record.trace_id,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )


def attempt_response(record: ExperimentItemAttemptRecord) -> ExperimentItemAttempt:
    return ExperimentItemAttempt(
        id=record.id,
        experiment_id=record.experiment_id,
        case_id=record.dataset_case.case_key,
        repetition=record.repetition,
        attempt=record.attempt,
        external_run_id=record.external_run_id,
        status=ExecutionStatus(record.status),
        output=record.output,
        usage=record.usage,
        runtime_metadata=record.runtime_metadata,
        error_type=record.error_type,
        error_message=record.error_message,
        trace_id=record.source_trace_id,
        evidence_status=cast(
            Literal["pending", "complete", "incomplete", "not_required"],
            record.evidence_status,
        ),
        evidence_reasons=record.evidence_reasons,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )


def canonical_submission_hash(target_status: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"status": target_status, **payload},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def frozen_case(run: EvaluationRunRecord, public_case_id: str) -> dict[str, Any]:
    manifest = (run.configuration_snapshot.get("dataset_version") or {}).get(
        "case_manifest", []
    )
    case_data = next(
        (
            item
            for item in manifest
            if isinstance(item, dict) and item.get("case_id") == public_case_id
        ),
        None,
    )
    if case_data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="case not found")
    return case_data


def locked_attempt(
    db: Session,
    run: EvaluationRunRecord,
    item_id: str,
) -> ExperimentItemAttemptRecord:
    item = db.scalar(
        select(ExperimentItemAttemptRecord)
        .where(
            ExperimentItemAttemptRecord.id == item_id,
            ExperimentItemAttemptRecord.experiment_id == run.id,
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="item not found")
    return item


def resolve_trace_link(
    db: Session,
    run: EvaluationRunRecord,
    item: ExperimentItemAttemptRecord,
    public_trace_id: str | None,
) -> str | None:
    if public_trace_id is None:
        return None
    trace = db.scalar(
        select(TraceRecord).where(
            TraceRecord.project_id == run.project_id,
            TraceRecord.run_id == run.id,
            TraceRecord.case_id == item.dataset_case.case_key,
            TraceRecord.trace_id == public_trace_id,
        )
    )
    if trace is not None:
        return trace.id
    conflicting_trace = db.scalar(
        select(TraceRecord).where(
            TraceRecord.project_id == run.project_id,
            TraceRecord.trace_id == public_trace_id,
        )
    )
    if conflicting_trace is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="trace does not belong to this Experiment Item",
        )
    return None


def evidence_reasons(
    run: EvaluationRunRecord,
    item: ExperimentItemAttemptRecord,
) -> list[str]:
    trace = item.trace
    if trace is None:
        return ["linked Trace is missing"]
    spans = trace.spans
    roots = [span for span in spans if span.parent_span_id is None]
    reasons: list[str] = []
    if not roots:
        reasons.append("root Agent Span is missing")
    if run.evidence_policy == EvidencePolicy.TRACE_REQUIRED.value:
        return reasons

    llm_spans = [span for span in spans if span.kind == "llm"]
    if not llm_spans:
        reasons.append("LLM Span is missing")
    else:
        model_keys = {
            "llm.model_name",
            "gen_ai.request.model",
            "gen_ai.response.model",
            "openinference.llm.model_name",
        }
        if not any(any(key in (span.attributes or {}) for key in model_keys) for span in llm_spans):
            reasons.append("LLM model identity is missing")
        if not any(
            bool(span.usage)
            or (span.attributes or {}).get("agent_eval.usage.present") is True
            for span in llm_spans
        ):
            reasons.append("LLM provider usage marker is missing")
        if any(span.started_at is None or span.ended_at is None for span in llm_spans):
            reasons.append("LLM timing is incomplete")

    case_data = frozen_case(run, item.dataset_case.case_key)
    if run.evidence_policy == EvidencePolicy.TOOL_TRAJECTORY_REQUIRED.value and case_data.get(
        "expected_tools"
    ):
        kinds = {span.kind for span in spans}
        if "tool" not in kinds:
            reasons.append("Tool Span is missing")
        if "tool_result" not in kinds:
            reasons.append("Tool Result Span is missing")
    if run.evidence_policy == EvidencePolicy.RAG_TRAJECTORY_REQUIRED.value and case_data.get(
        "retrieval_context"
    ):
        retrieval_spans = [span for span in spans if span.kind == "retrieval"]
        if not retrieval_spans:
            reasons.append("Retriever Span is missing")
        elif not any(
            (span.attributes or {}).get("retrieval.document_ids") for span in retrieval_spans
        ):
            reasons.append("retrieved document IDs are missing")
    return reasons


def missing_evidence_scores(
    db: Session,
    run: EvaluationRunRecord,
    item: ExperimentItemAttemptRecord,
) -> None:
    existing = {
        row.evaluator_version_id
        for row in db.scalars(
            select(ScoreRecord).where(
                ScoreRecord.run_id == run.id,
                ScoreRecord.case_id == item.dataset_case.case_key,
                ScoreRecord.repetition == item.repetition,
            )
        ).all()
    }
    source_by_type = {
        "deterministic": "deterministic",
        "llm_judge": "llm_judge",
        "adapter": "adapter",
        "human": "automated",
    }
    for evaluator in run.configuration_snapshot.get("evaluators", []):
        evaluator_id = str(evaluator["id"])
        if evaluator_id in existing:
            continue
        db.add(
            ScoreRecord(
                id=new_id(),
                run_id=run.id,
                case_id=item.dataset_case.case_key,
                experiment_item_id=item.id,
                repetition=item.repetition,
                attempt=item.attempt,
                metric_name=str(evaluator["name"]),
                evaluator_version_id=evaluator_id,
                trace_id=item.trace_id,
                source=source_by_type.get(str(evaluator["evaluator_type"]), "automated"),
                status="missing",
                value=None,
                passed=None,
                explanation="; ".join(item.evidence_reasons),
                evidence=[{"reason": reason} for reason in item.evidence_reasons],
                threshold=evaluator.get("default_threshold"),
                direction=str(evaluator.get("direction") or "higher_is_better"),
            )
        )


def project_attempt_execution(
    db: Session,
    run: EvaluationRunRecord,
    item: ExperimentItemAttemptRecord,
) -> CaseExecutionRecord:
    execution = db.scalar(
        select(CaseExecutionRecord).where(
            CaseExecutionRecord.run_id == run.id,
            CaseExecutionRecord.case_id == item.case_id,
        )
    )
    if execution is None:
        raise RuntimeError("Experiment scoring projection is missing")
    execution.status = item.status
    execution.attempt = item.attempt
    execution.output = item.output
    execution.usage = item.usage
    execution.error_type = item.error_type
    execution.error_message = item.error_message
    execution.trace_id = item.trace_id
    execution.started_at = item.started_at
    execution.finished_at = item.finished_at
    execution.tool_calls = [
        {
            "name": span.name,
            "arguments": span.input if isinstance(span.input, dict) else {},
            "order": (span.attributes or {}).get("tool.order"),
        }
        for span in (item.trace.spans if item.trace is not None else [])
        if span.kind == "tool"
    ]
    return execution


def score_completed_attempt(
    db: Session,
    run: EvaluationRunRecord,
    item: ExperimentItemAttemptRecord,
    execution: CaseExecutionRecord,
    settings: Settings,
) -> None:
    existing_evaluator_ids = set(
        db.scalars(
            select(ScoreRecord.evaluator_version_id).where(
                ScoreRecord.run_id == run.id,
                ScoreRecord.case_id == item.dataset_case.case_key,
                ScoreRecord.repetition == item.repetition,
            )
        ).all()
    )
    evaluator_ids = {
        str(snapshot["id"])
        for snapshot in run.configuration_snapshot.get("evaluators", [])
    }
    if evaluator_ids <= existing_evaluator_ids:
        return
    if item.trace is None:
        raise RuntimeError("Experiment scoring Trace is missing")
    from agent_eval_api.evaluation import evaluate_and_persist_scores
    from agent_eval_api.traces import trace_response

    asyncio.run(
        evaluate_and_persist_scores(
            db,
            run,
            execution,
            trace_response(item.trace, settings),
            settings,
            experiment_item_id=item.id,
            repetition=item.repetition,
            attempt=item.attempt,
        )
    )


def dispatch_pending_managed_judges(
    db: Session,
    run: EvaluationRunRecord,
    settings: Settings,
) -> int:
    from agent_eval_api.evaluation.dispatch import dispatch_managed_judge

    pending = list(
        db.scalars(
            select(ScoreRecord).where(
                ScoreRecord.run_id == run.id,
                ScoreRecord.status == "not_run",
            )
        ).all()
    )
    candidates: list[ScoreRecord] = []
    for score in pending:
        if not (
            isinstance(score.provenance, dict)
            and score.provenance.get("source") == "platform_provider"
        ):
            continue
        raw = score.raw_result if isinstance(score.raw_result, dict) else {}
        if raw.get("state") in {"dispatched", "running"}:
            continue
        # Make the score visible to a separate worker before publishing the
        # Redis message. Otherwise a fast worker can observe no row and return
        # `ignored` while the API transaction is still uncommitted.
        score.raw_result = {**raw, "state": "queued"}
        score.explanation = "managed Judge is queued for the evaluation worker"
        candidates.append(score)

    if not candidates:
        return 0

    db.commit()

    dispatched = 0
    for score in candidates:
        task_id = dispatch_managed_judge(redis_url=settings.redis_url, score_id=score.id)
        current = db.get(ScoreRecord, score.id)
        if current is None or current.status != ScoreStatus.NOT_RUN.value:
            # The worker may have claimed and completed the score before the
            # producer received the task id. Never overwrite that terminal row.
            continue
        current_raw = current.raw_result if isinstance(current.raw_result, dict) else {}
        if current_raw.get("state") == "running":
            continue
        current.raw_result = {
            **current_raw,
            "state": "dispatched",
            "task_id": task_id,
        }
        current.explanation = "managed Judge was dispatched to the evaluation worker"
        db.commit()
        dispatched += 1
    return dispatched


def pending_managed_judge_count(db: Session, run: EvaluationRunRecord) -> int:
    return sum(
        1
        for score in db.scalars(
            select(ScoreRecord).where(
                ScoreRecord.run_id == run.id,
                ScoreRecord.status == "not_run",
            )
        ).all()
        if isinstance(score.provenance, dict)
        and score.provenance.get("source") == "platform_provider"
    )


def terminalize_attempt(
    db: Session,
    run: EvaluationRunRecord,
    item: ExperimentItemAttemptRecord,
    target_status: ExecutionStatus,
    submission: dict[str, Any],
) -> ExperimentItemAttempt:
    result_hash = canonical_submission_hash(target_status.value, submission)
    if item.status in {
        ExecutionStatus.COMPLETED.value,
        ExecutionStatus.FAILED.value,
        ExecutionStatus.CANCELLED.value,
    }:
        if item.status == target_status.value and item.result_hash == result_hash:
            return attempt_response(item)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="terminal item submission conflicts with immutable evidence",
        )
    if item.status != ExecutionStatus.RUNNING.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"expected running item, found {item.status}",
        )

    public_trace_id = submission.get("trace_id")
    item.source_trace_id = public_trace_id
    item.trace_id = resolve_trace_link(db, run, item, public_trace_id)
    item.output = submission.get("output")
    item.usage = submission.get("usage", {})
    item.runtime_metadata = {
        **item.runtime_metadata,
        **submission.get("runtime_metadata", {}),
    }
    item.error_type = submission.get("error_type")
    item.error_message = submission.get("error_message")
    item.result_hash = result_hash
    item.status = target_status.value
    item.finished_at = datetime.now(UTC)
    db.commit()
    db.refresh(item)
    return attempt_response(item)


@router.post("", response_model=EvaluationRun, status_code=status.HTTP_201_CREATED)
@experiments_router.post("", response_model=EvaluationRun, status_code=status.HTTP_201_CREATED)
def create_run(
    project_id: str,
    payload: EvaluationRunCreateRequest,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> EvaluationRun:
    get_project(db, project_id)
    if payload.execution_mode not in {
        ExperimentExecutionMode.SDK_TASK,
        ExperimentExecutionMode.OTEL,
        ExperimentExecutionMode.REMOTE_UPLOAD,
        ExperimentExecutionMode.REMOTE_TRIGGER,
    }:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"execution mode '{payload.execution_mode.value}' is not enabled; "
                "use the user-owned Python SDK with execution_mode='sdk_task' or "
                "an instrumented Agent with execution_mode='otel' or "
                "a language-neutral runtime with execution_mode='remote_upload'"
            ),
        )
    agent, agent_version = get_project_agent_version(db, project_id, payload.agent_version_id)
    if (agent is not None and not agent.active) or not agent_version.enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="agent version must be enabled before starting a run",
        )
    if agent is not None or agent_version.endpoint_config is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "legacy HTTP Agent releases cannot create experiments; migrate the "
                "release to remote_upload or remote_trigger, or register an "
                "endpoint-independent Agent Release"
            ),
        )
    dataset_version = get_project_dataset_version(db, project_id, payload.dataset_version_id)
    remote_trigger: RemoteTriggerRecord | None = None
    if payload.execution_mode == ExperimentExecutionMode.REMOTE_TRIGGER:
        remote_trigger = db.scalar(
            select(RemoteTriggerRecord).where(
                RemoteTriggerRecord.project_id == project_id,
                RemoteTriggerRecord.dataset_id == dataset_version.dataset_id,
                RemoteTriggerRecord.enabled.is_(True),
            )
        )
        if remote_trigger is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "an enabled Dataset remote trigger is required for "
                    "execution_mode='remote_trigger'"
                ),
            )
    evaluators = get_project_evaluators(db, project_id, payload.evaluator_version_ids)
    baseline = None
    if payload.baseline_run_id is not None:
        baseline = db.scalar(
            select(EvaluationRunRecord).where(
                EvaluationRunRecord.id == payload.baseline_run_id,
                EvaluationRunRecord.project_id == project_id,
            )
        )
        if baseline is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="baseline run not found",
            )
        if baseline.dataset_version_id != dataset_version.id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "message": "baseline run must use the same dataset version",
                    "baseline_run_id": baseline.id,
                    "dataset_version_id": dataset_version.id,
                },
            )
    disabled = [evaluator.id for evaluator in evaluators if not evaluator.enabled]
    if disabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "evaluators must be enabled", "evaluator_version_ids": disabled},
        )
    incompatible = [
        evaluator.id
        for evaluator in evaluators
        if agent_version.agent_type not in evaluator.supported_agent_types
    ]
    if incompatible:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "evaluator does not support the selected agent type",
                "agent_type": agent_version.agent_type,
                "evaluator_version_ids": incompatible,
            },
        )

    cases = db.scalars(
        select(DatasetCaseRecord)
        .where(DatasetCaseRecord.dataset_version_id == dataset_version.id)
        .order_by(DatasetCaseRecord.case_key)
    ).all()
    validate_evaluator_requirements(evaluators, cases)

    evaluator_connections: dict[str, EvaluatorConnectionRecord] = {}
    connection_ids = {
        evaluator.evaluator_connection_id
        for evaluator in evaluators
        if evaluator.evaluator_connection_id is not None
    }
    if connection_ids:
        connections = db.scalars(
            select(EvaluatorConnectionRecord).where(
                EvaluatorConnectionRecord.project_id == project_id,
                EvaluatorConnectionRecord.id.in_(connection_ids),
            )
        ).all()
        evaluator_connections = {connection.id: connection for connection in connections}
        missing_connections = sorted(connection_ids - evaluator_connections.keys())
        if missing_connections:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "message": "evaluator connection not found",
                    "evaluator_connection_ids": missing_connections,
                },
            )

    provider_connections: dict[str, ProviderConnectionRecord] = {}
    provider_ids = {
        evaluator.provider_connection_id
        for evaluator in evaluators
        if evaluator.provider_connection_id is not None
    }
    if provider_ids:
        providers = db.scalars(
            select(ProviderConnectionRecord).where(
                ProviderConnectionRecord.project_id == project_id,
                ProviderConnectionRecord.id.in_(provider_ids),
                ProviderConnectionRecord.status == "active",
                ProviderConnectionRecord.enabled.is_(True),
            )
        ).all()
        provider_connections = {provider.id: provider for provider in providers}
        unavailable_providers = sorted(provider_ids - provider_connections.keys())
        if unavailable_providers:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "message": "managed Judge provider connections must be active",
                    "provider_connection_ids": unavailable_providers,
                },
            )

    execution_options = payload.execution_options.model_copy(deep=True)
    baseline_snapshot = None
    if baseline is not None:
        baseline_evaluators = baseline.configuration_snapshot.get("evaluators", [])
        baseline_snapshot = {
            "run_id": baseline.id,
            "dataset_version_id": baseline.dataset_version_id,
            "agent_version_id": baseline.agent_version_id,
            "evaluator_version_ids": [item.get("id") for item in baseline_evaluators],
            "created_at": baseline.created_at.isoformat(),
        }

    run = EvaluationRunRecord(
        id=new_id(),
        project_id=project_id,
        name=payload.name,
        agent_version_id=agent_version.id,
        dataset_version_id=dataset_version.id,
        baseline_run_id=baseline.id if baseline is not None else None,
        execution_mode=payload.execution_mode.value,
        evidence_policy=payload.evidence_policy.value,
        status=RunStatus.QUEUED.value,
        total_cases=len(cases) * execution_options.repetitions,
        configuration_snapshot={
            "agent_version": agent_snapshot(agent, agent_version),
            "dataset_version": {
                "id": dataset_version.id,
                "dataset_id": dataset_version.dataset_id,
                "version": dataset_version.version,
                "metadata": dataset_version.metadata_json,
                "case_count": len(cases),
                "case_manifest": [case_snapshot(case) for case in cases],
            },
            "evaluators": [
                evaluator_snapshot(
                    evaluator,
                    (
                        evaluator_connections.get(evaluator.evaluator_connection_id)
                        if evaluator.evaluator_connection_id is not None
                        else None
                    ),
                    (
                        provider_connections.get(evaluator.provider_connection_id)
                        if evaluator.provider_connection_id is not None
                        else None
                    ),
                )
                for evaluator in evaluators
            ],
            "execution_options": execution_options.model_dump(mode="json"),
            "execution_mode": payload.execution_mode.value,
            "evidence_policy": payload.evidence_policy.value,
            "baseline": baseline_snapshot,
        },
    )
    db.add(run)
    db.add_all(
        [
            CaseExecutionRecord(
                id=new_id(),
                run_id=run.id,
                case_id=case.id,
                status=ExecutionStatus.QUEUED.value,
            )
            for case in cases
        ]
    )
    if not cases:
        from agent_eval_api.run_state import transition_run

        transition_run(run, RunStatus.RUNNING)
        transition_run(run, RunStatus.COMPLETED)
    db.commit()
    db.refresh(run)
    if remote_trigger is not None:
        delivery_record = RemoteTriggerDeliveryRecord(
            id=new_id(),
            trigger_id=remote_trigger.id,
            experiment_id=run.id,
            delivery_id=new_id(),
            status="pending",
        )
        db.add(delivery_record)
        db.commit()
        db.refresh(delivery_record)
        try:
            delivery = build_experiment_trigger_delivery(
                trigger=remote_trigger,
                experiment_id=run.id,
                experiment_name=run.name,
                dataset_version_id=dataset_version.id,
                dataset_version=dataset_version.version,
                agent_release=agent_version.release_identity,
                evidence_policy=run.evidence_policy,
                callback_base_url=str(request.base_url),
                settings=settings,
                delivery_id=delivery_record.delivery_id,
            )
            deliver_experiment_trigger(
                db=db,
                trigger=remote_trigger,
                record=delivery_record,
                delivery=delivery,
            )
        except RemoteTriggerDeliveryError as exc:
            delivery_record.status = "failed"
            delivery_record.last_error_type = exc.error_type
            delivery_record.last_error_message = "remote trigger signing is unavailable"
            db.commit()
    return run_response(run)


@router.get("", response_model=ExperimentPage)
@experiments_router.get("", response_model=ExperimentPage)
def list_runs(
    project_id: str,
    run_status: RunStatus | None = Query(default=None, alias="status"),  # noqa: B008
    execution_mode: ExperimentExecutionMode | None = None,
    query: str | None = Query(default=None, min_length=1, max_length=200),  # noqa: B008
    offset: int = Query(default=0, ge=0),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=200),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> ExperimentPage:
    get_project(db, project_id)
    filters = [EvaluationRunRecord.project_id == project_id]
    if run_status is not None:
        filters.append(EvaluationRunRecord.status == run_status.value)
    if execution_mode is not None:
        filters.append(EvaluationRunRecord.execution_mode == execution_mode.value)
    if query is not None:
        pattern = f"%{query.strip()}%"
        filters.append(
            or_(
                EvaluationRunRecord.id.ilike(pattern),
                EvaluationRunRecord.name.ilike(pattern),
                EvaluationRunRecord.agent_version_id.ilike(pattern),
                EvaluationRunRecord.dataset_version_id.ilike(pattern),
            )
        )
    total = (
        db.scalar(select(func.count()).select_from(EvaluationRunRecord).where(*filters))
        or 0
    )
    records = db.scalars(
        select(EvaluationRunRecord)
        .where(*filters)
        .order_by(EvaluationRunRecord.created_at.desc(), EvaluationRunRecord.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    next_offset = offset + len(records) if offset + len(records) < total else None
    return ExperimentPage(
        items=[run_response(record) for record in records],
        total=total,
        offset=offset,
        limit=limit,
        next_offset=next_offset,
    )


def get_run(db: Session, project_id: str, run_id: str) -> EvaluationRunRecord:
    run = db.scalar(
        select(EvaluationRunRecord).where(
            EvaluationRunRecord.project_id == project_id,
            EvaluationRunRecord.id == run_id,
        )
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="evaluation run not found"
        )
    return run


def run_detail_response(db: Session, run: EvaluationRunRecord) -> EvaluationRunDetail:
    executions = db.scalars(
        select(CaseExecutionRecord)
        .join(DatasetCaseRecord, CaseExecutionRecord.case_id == DatasetCaseRecord.id)
        .where(CaseExecutionRecord.run_id == run.id)
        .order_by(DatasetCaseRecord.case_key)
    ).all()
    return EvaluationRunDetail(
        **run_response(run).model_dump(),
        case_executions=[execution_response(execution) for execution in executions],
    )


@router.get("/{run_id}", response_model=EvaluationRunDetail)
@experiments_router.get("/{run_id}", response_model=EvaluationRunDetail)
def read_run(
    project_id: str,
    run_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> EvaluationRunDetail:
    run = get_run(db, project_id, run_id)
    return run_detail_response(db, run)


@experiments_router.get(
    "/{run_id}/manifest",
    response_model=ExperimentManifestPage,
)
def read_experiment_manifest(
    project_id: str,
    run_id: str,
    offset: int = Query(default=0, ge=0),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=200),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> ExperimentManifestPage:
    """Read Cases from the frozen Experiment definition, never a mutable Dataset alias."""

    run = get_run(db, project_id, run_id)
    dataset_snapshot = run.configuration_snapshot.get("dataset_version") or {}
    manifest = dataset_snapshot.get("case_manifest") or []
    if not isinstance(manifest, list):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="experiment case manifest is invalid",
        )

    selected = manifest[offset : offset + limit]
    internal_case_ids = [item.get("id") for item in selected if isinstance(item, dict)]
    attempt_records = db.scalars(
        select(ExperimentItemAttemptRecord)
        .where(
            ExperimentItemAttemptRecord.experiment_id == run.id,
            ExperimentItemAttemptRecord.case_id.in_(internal_case_ids),
        )
        .order_by(
            ExperimentItemAttemptRecord.case_id,
            ExperimentItemAttemptRecord.repetition,
            ExperimentItemAttemptRecord.attempt,
        )
    ).all()
    attempts_by_case: dict[str, list[ExperimentItemAttempt]] = {}
    for record in attempt_records:
        attempts_by_case.setdefault(record.case_id, []).append(attempt_response(record))

    items = [
        ExperimentManifestItem(
            case=DatasetCase.model_validate(
                {
                    **{
                        key: value
                        for key, value in case_data.items()
                        if key not in {"id", "case_id"}
                    },
                    "id": case_data["case_id"],
                }
            ),
            attempts=attempts_by_case.get(case_data["id"], []),
        )
        for case_data in selected
    ]
    next_offset = offset + len(items)
    return ExperimentManifestPage(
        experiment_id=run.id,
        dataset_version_id=run.dataset_version_id,
        items=items,
        total=len(manifest),
        offset=offset,
        limit=limit,
        next_offset=next_offset if next_offset < len(manifest) else None,
    )


@experiments_router.post(
    "/{run_id}/items/start",
    response_model=ExperimentItemAttempt,
    status_code=status.HTTP_201_CREATED,
)
def start_experiment_item(
    project_id: str,
    run_id: str,
    payload: ExperimentItemStartRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> ExperimentItemAttempt:
    run = get_run(db, project_id, run_id)
    if run.status not in {RunStatus.QUEUED.value, RunStatus.RUNNING.value}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"cannot start an item in a {run.status} Experiment",
        )
    case_data = frozen_case(run, payload.case_id)
    repetitions = int(
        (run.configuration_snapshot.get("execution_options") or {}).get("repetitions", 1)
    )
    if payload.repetition > repetitions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="repetition exceeds the frozen Experiment definition",
        )

    def identity_matches(existing: ExperimentItemAttemptRecord) -> bool:
        return (
            existing.case_id == case_data["id"]
            and existing.repetition == payload.repetition
            and existing.attempt == payload.attempt
            and existing.runtime_metadata == payload.runtime_metadata
        )

    existing = db.scalar(
        select(ExperimentItemAttemptRecord).where(
            ExperimentItemAttemptRecord.experiment_id == run.id,
            ExperimentItemAttemptRecord.external_run_id == payload.external_run_id,
        )
    )
    if existing is not None:
        if identity_matches(existing):
            return attempt_response(existing)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="external run ID conflicts with an existing item",
        )

    previous = db.scalars(
        select(ExperimentItemAttemptRecord)
        .where(
            ExperimentItemAttemptRecord.experiment_id == run.id,
            ExperimentItemAttemptRecord.case_id == case_data["id"],
            ExperimentItemAttemptRecord.repetition == payload.repetition,
        )
        .order_by(ExperimentItemAttemptRecord.attempt.desc())
    ).first()
    expected_attempt = 1 if previous is None else previous.attempt + 1
    if payload.attempt != expected_attempt:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"expected attempt {expected_attempt}",
        )
    if previous is not None and previous.status not in {
        ExecutionStatus.FAILED.value,
        ExecutionStatus.CANCELLED.value,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a new attempt requires the previous attempt to fail or be cancelled",
        )

    item = ExperimentItemAttemptRecord(
        id=new_id(),
        experiment_id=run.id,
        case_id=case_data["id"],
        repetition=payload.repetition,
        attempt=payload.attempt,
        external_run_id=payload.external_run_id,
        status=ExecutionStatus.RUNNING.value,
        runtime_metadata=payload.runtime_metadata,
        started_at=datetime.now(UTC),
    )
    db.add(item)
    if run.status == RunStatus.QUEUED.value:
        from agent_eval_api.run_state import transition_run

        transition_run(run, RunStatus.RUNNING)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raced = db.scalar(
            select(ExperimentItemAttemptRecord).where(
                ExperimentItemAttemptRecord.experiment_id == run.id,
                ExperimentItemAttemptRecord.external_run_id == payload.external_run_id,
            )
        )
        if raced is not None and identity_matches(raced):
            return attempt_response(raced)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="item start lost a concurrent attempt race",
        ) from exc
    db.refresh(item)
    return attempt_response(item)


@experiments_router.post(
    "/{run_id}/items/{item_id}/complete",
    response_model=ExperimentItemAttempt,
)
def complete_experiment_item(
    project_id: str,
    run_id: str,
    item_id: str,
    payload: ExperimentItemCompleteRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> ExperimentItemAttempt:
    run = get_run(db, project_id, run_id)
    item = locked_attempt(db, run, item_id)
    return terminalize_attempt(
        db,
        run,
        item,
        ExecutionStatus.COMPLETED,
        payload.model_dump(mode="json", exclude={"expected_status"}),
    )


@experiments_router.post(
    "/{run_id}/items/{item_id}/fail",
    response_model=ExperimentItemAttempt,
)
def fail_experiment_item(
    project_id: str,
    run_id: str,
    item_id: str,
    payload: ExperimentItemFailRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> ExperimentItemAttempt:
    run = get_run(db, project_id, run_id)
    item = locked_attempt(db, run, item_id)
    return terminalize_attempt(
        db,
        run,
        item,
        ExecutionStatus.FAILED,
        payload.model_dump(mode="json", exclude={"expected_status"}),
    )


@experiments_router.post(
    "/{run_id}/items/{item_id}/cancel",
    response_model=ExperimentItemAttempt,
)
def cancel_experiment_item(
    project_id: str,
    run_id: str,
    item_id: str,
    payload: ExperimentItemCancelRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> ExperimentItemAttempt:
    run = get_run(db, project_id, run_id)
    item = locked_attempt(db, run, item_id)
    return terminalize_attempt(
        db,
        run,
        item,
        ExecutionStatus.CANCELLED,
        payload.model_dump(mode="json", exclude={"expected_status"}),
    )


@experiments_router.post("/{run_id}/finalize", response_model=EvaluationRun)
def finalize_experiment(
    project_id: str,
    run_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> EvaluationRun:
    run = get_run(db, project_id, run_id)
    if run.status in {
        RunStatus.COMPLETED.value,
        RunStatus.PARTIAL.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    }:
        return run_response(run)

    manifest = (run.configuration_snapshot.get("dataset_version") or {}).get(
        "case_manifest", []
    )
    repetitions = int(
        (run.configuration_snapshot.get("execution_options") or {}).get("repetitions", 1)
    )
    expected_positions = [
        (case_data["id"], repetition)
        for case_data in manifest
        for repetition in range(1, repetitions + 1)
    ]
    expected_position_set = set(expected_positions)
    attempts = db.scalars(
        select(ExperimentItemAttemptRecord).where(
            ExperimentItemAttemptRecord.experiment_id == run.id
        )
    ).all()
    latest: dict[tuple[str, int], ExperimentItemAttemptRecord] = {}
    for item in attempts:
        key = (item.case_id, item.repetition)
        if key not in latest or item.attempt > latest[key].attempt:
            latest[key] = item
    unfinished = expected_position_set - latest.keys()
    unfinished.update(
        key
        for key, item in latest.items()
        if item.status not in {
            ExecutionStatus.COMPLETED.value,
            ExecutionStatus.FAILED.value,
            ExecutionStatus.CANCELLED.value,
        }
    )
    if unfinished:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Experiment has unfinished items", "count": len(unfinished)},
        )

    relevant = [latest[key] for key in expected_positions]
    waiting_for_trace = [
        item
        for item in relevant
        if item.status == ExecutionStatus.COMPLETED.value
        and item.source_trace_id is not None
        and item.trace_id is None
    ]
    if waiting_for_trace:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Experiment is waiting for Trace ingestion",
                "count": len(waiting_for_trace),
            },
        )
    incomplete = 0
    for item in relevant:
        execution = project_attempt_execution(db, run, item)
        if item.status != ExecutionStatus.COMPLETED.value:
            item.evidence_status = "not_required"
            item.evidence_reasons = []
            continue
        reasons = evidence_reasons(run, item)
        item.evidence_reasons = reasons
        item.evidence_status = "incomplete" if reasons else "complete"
        incomplete += bool(reasons)
        if reasons:
            missing_evidence_scores(db, run, item)
        else:
            score_completed_attempt(db, run, item, execution, settings)
    from agent_eval_api.evaluation import aggregate_run_scores

    aggregate_run_scores(db, run)
    run.total_cases = len(expected_positions)
    run.completed_cases = sum(
        item.status == ExecutionStatus.COMPLETED.value for item in relevant
    )
    run.failed_cases = len(relevant) - run.completed_cases
    from agent_eval_api.run_state import transition_run

    if run.status == RunStatus.QUEUED.value:
        transition_run(run, RunStatus.RUNNING)
    dispatched = dispatch_pending_managed_judges(db, run, settings)
    pending_judges = pending_managed_judge_count(db, run)
    if dispatched or pending_judges:
        db.commit()
        db.refresh(run)
        return run_response(run)
    if incomplete:
        target = RunStatus.PARTIAL
        run.failed_cases += incomplete
        run.completed_cases -= incomplete
    elif not relevant or run.failed_cases == 0:
        target = RunStatus.COMPLETED
    elif run.completed_cases == 0:
        target = RunStatus.FAILED
    else:
        target = RunStatus.PARTIAL
    transition_run(run, target)
    db.commit()
    db.refresh(run)
    return run_response(run)


@router.post("/{run_id}/cancel", response_model=EvaluationRunDetail)
@experiments_router.post("/{run_id}/cancel", response_model=EvaluationRunDetail)
def cancel_run(
    project_id: str,
    run_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> EvaluationRunDetail:
    """Cancel queued work and make running workers discard late results."""

    run = db.scalar(
        select(EvaluationRunRecord)
        .where(
            EvaluationRunRecord.project_id == project_id,
            EvaluationRunRecord.id == run_id,
        )
        .with_for_update()
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="evaluation run not found"
        )
    current = RunStatus(run.status)
    if current is RunStatus.CANCELLED:
        return run_detail_response(db, run)
    if current not in {RunStatus.QUEUED, RunStatus.RUNNING}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"cannot cancel a {current.value} evaluation run",
        )

    executions = db.scalars(
        select(CaseExecutionRecord)
        .where(
            CaseExecutionRecord.run_id == run.id,
            CaseExecutionRecord.status.in_(
                [ExecutionStatus.QUEUED.value, ExecutionStatus.RUNNING.value]
            ),
        )
        .with_for_update()
    ).all()
    from agent_eval_api.run_state import transition_execution, transition_run

    for execution in executions:
        transition_execution(execution, ExecutionStatus.CANCELLED)
    transition_run(run, RunStatus.CANCELLED)
    db.commit()
    db.refresh(run)
    return run_detail_response(db, run)
