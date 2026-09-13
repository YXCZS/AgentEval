"""Evaluate one finished case and atomically persist normalized sample Scores."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from agent_eval_api.contracts import (
    AgentType,
    CaseExecution,
    DatasetCase,
    EvaluatorType,
    EvaluatorVersion,
    ExecutionStatus,
    ExternalScoreProvenance,
    JudgeSamplingParameters,
    Score,
    ScoreDirection,
    ScoreSource,
    ScoreStatus,
    Trace,
    TraceSpanKind,
)
from agent_eval_api.db import (
    CaseExecutionRecord,
    EvaluationRunRecord,
    ScoreRecord,
    TraceSpanRecord,
    new_id,
)
from agent_eval_api.settings import Settings
from agent_eval_api.trace_privacy import PrivacyStats, sanitize_value

from .adapters import AdapterRunner, ThirdPartyAdapterError, evaluate_adapter
from .base import EvaluationContext, EvaluatorConfigurationError, EvaluatorOutcome
from .deterministic import deterministic_evaluator_key, evaluate_deterministic
from .external_protocols import ExternalJudgeConfig, ExternalProtocolError
from .judge import evaluate_llm_judge


def _case(record: CaseExecutionRecord) -> DatasetCase:
    case = record.dataset_case
    return DatasetCase.model_validate(
        {
            "id": case.case_key,
            "input": case.input_json,
            "variables": case.variables,
            "expected_output": case.expected_output,
            "output_schema": case.output_schema,
            "criteria": case.criteria,
            "expected_tools": case.expected_tools,
            "expected_state": case.expected_state,
            "retrieval_context": case.retrieval_context,
            "messages": case.messages,
            "metadata": case.metadata_json,
            "source_trace_id": case.source_trace_id,
        }
    )


def _execution(record: CaseExecutionRecord) -> CaseExecution:
    return CaseExecution.model_validate(
        {
            "id": record.id,
            "run_id": record.run_id,
            "case_id": record.dataset_case.case_key,
            "status": ExecutionStatus(record.status),
            "attempt": record.attempt,
            "output": record.output,
            "tool_calls": record.tool_calls,
            "usage": record.usage,
            "error_type": record.error_type,
            "error_message": record.error_message,
            "trace_id": record.trace_id,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
        }
    )


def _evaluator(snapshot: Mapping[str, Any]) -> EvaluatorVersion:
    """Rebuild an evaluator exclusively from the Experiment snapshot.

    A completed or queued run must not change behavior when the current
    evaluator record is edited after the run was created.
    """

    supported_agent_types: list[AgentType] = []
    for item in snapshot.get("supported_agent_types", []):
        try:
            supported_agent_types.append(AgentType(item))
        except ValueError:
            supported_agent_types.append(AgentType.CUSTOM)
    if not supported_agent_types:
        supported_agent_types = [AgentType.CUSTOM]
    return EvaluatorVersion(
        id=str(snapshot["id"]),
        name=str(snapshot["name"]),
        version=str(snapshot["version"]),
        evaluator_type=EvaluatorType(snapshot["evaluator_type"]),
        requires=list(snapshot.get("requires", [])),
        supported_agent_types=supported_agent_types,
        score_min=snapshot.get("score_min"),
        score_max=snapshot.get("score_max"),
        direction=ScoreDirection(snapshot["direction"]),
        default_threshold=snapshot.get("default_threshold"),
        rubric=snapshot.get("rubric"),
        evaluator_connection_id=(
            str(snapshot["evaluator_connection"]["id"])
            if snapshot.get("evaluator_connection")
            else snapshot.get("evaluator_connection_id")
        ),
        provider_connection_id=(
            str(snapshot["provider_connection"]["id"])
            if snapshot.get("provider_connection")
            else snapshot.get("provider_connection_id")
        ),
        judge_model=snapshot.get("judge_model"),
        prompt_template=snapshot.get("prompt_template"),
        output_schema=(
            dict(snapshot["output_schema"])
            if isinstance(snapshot.get("output_schema"), Mapping)
            else None
        ),
        sampling_parameters=(
            JudgeSamplingParameters.model_validate(snapshot["sampling_parameters"])
            if snapshot.get("sampling_parameters") is not None
            else None
        ),
        config=dict(snapshot.get("config") or {}),
        enabled=bool(snapshot.get("enabled", True)),
    )


def _external_judge_config(
    evaluator_snapshot: Mapping[str, Any],
    *,
    max_retries: int = 2,
    retry_backoff_seconds: float = 0.2,
) -> ExternalJudgeConfig:
    connection = evaluator_snapshot.get("evaluator_connection")
    if not isinstance(connection, Mapping):
        raise EvaluatorConfigurationError(
            "llm judge requires a user-managed evaluator connection"
        )
    endpoint = connection.get("endpoint")
    if not endpoint:
        raise EvaluatorConfigurationError(
            "llm judge connection requires an endpoint"
        )
    connection_id = connection.get("id")
    if not connection_id:
        raise EvaluatorConfigurationError(
            "llm judge connection requires an immutable connection id"
        )
    return ExternalJudgeConfig(
        connection_id=str(connection_id),
        endpoint=str(endpoint),
        timeout_seconds=float(connection.get("timeout_seconds", 60.0)),
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )


def _external_judge_secret(
    settings: Settings,
    auth_ref: Any,
    resolver: Callable[[str], str | None] | None,
) -> str:
    if not isinstance(auth_ref, str) or not auth_ref.strip():
        raise EvaluatorConfigurationError(
            "external Judge connection requires a signing secret reference"
        )
    try:
        if resolver is not None:
            secret = resolver(auth_ref)
        else:
            configured = settings.external_evaluator_secrets.get(auth_ref)
            secret = configured.get_secret_value() if configured is not None else None
    except Exception as exc:
        raise EvaluatorConfigurationError(
            "external Judge signing secret could not be resolved"
        ) from exc
    if not isinstance(secret, str) or not secret:
        raise EvaluatorConfigurationError(
            "external Judge signing secret is unavailable"
        )
    return secret


def _error_outcome(
    evaluator: EvaluatorVersion,
    exc: Exception,
    evaluator_snapshot: Mapping[str, Any],
) -> EvaluatorOutcome:
    error_type = getattr(exc, "error_type", "evaluation_error")
    attempts = getattr(exc, "attempts", None)
    provenance: ExternalScoreProvenance | None = None
    raw_result: dict[str, Any] = {
        "error_type": str(error_type),
        "message": str(exc),
        **({"attempts": int(attempts)} if attempts is not None else {}),
    }
    connection = evaluator_snapshot.get("evaluator_connection")
    if evaluator.evaluator_type is EvaluatorType.LLM_JUDGE and isinstance(
        connection, Mapping
    ):
        provenance = ExternalScoreProvenance(
            source="external_judge",
            protocol="signed_http_json_v1",
            connection_id=str(connection.get("id") or "") or None,
            evaluator_version=f"{evaluator.name}@{evaluator.version}",
            model=str(evaluator.config.get("model") or "") or None,
            rubric_version=evaluator.version,
            prompt_template_version=(
                str(evaluator.config.get("prompt_template_version") or "") or None
            ),
            metadata={"state": "error"},
        )
        raw_result.update(
            protocol="signed_http_json_v1",
            provider="external_judge",
            state="error",
        )
    return EvaluatorOutcome(
        metric_name=evaluator.name,
        status=ScoreStatus.ERROR,
        passed=None,
        explanation=str(exc),
        provenance=provenance,
        raw_result=raw_result,
    )


def is_managed_judge_snapshot(snapshot: Mapping[str, Any]) -> bool:
    return (
        snapshot.get("evaluator_type") == EvaluatorType.LLM_JUDGE.value
        and isinstance(snapshot.get("provider_connection"), Mapping)
    )


def _pending_managed_judge_outcome(
    context: EvaluationContext,
    snapshot: Mapping[str, Any],
) -> EvaluatorOutcome:
    provider = snapshot.get("provider_connection")
    model = snapshot.get("judge_model")
    if not isinstance(provider, Mapping):
        raise EvaluatorConfigurationError("managed Judge provider snapshot is missing")
    return EvaluatorOutcome(
        metric_name=context.evaluator.name,
        status=ScoreStatus.NOT_RUN,
        passed=None,
        explanation="managed Judge is queued for asynchronous execution",
        provenance=ExternalScoreProvenance(
            source="platform_provider",
            protocol="openai_compatible_chat_completions",
            connection_id=str(provider["id"]),
            evaluator_version=f"{context.evaluator.name}@{context.evaluator.version}",
            model=str(model or provider.get("model") or "") or None,
            rubric_version=context.evaluator.version,
            prompt_template_version=context.evaluator.version,
            metadata={"state": "pending", "provider_connection_id": str(provider["id"])},
        ),
        raw_result={
            "protocol": "openai_compatible_chat_completions",
            "provider": "platform_provider",
            "state": "pending",
        },
    )


async def _evaluate(
    context: EvaluationContext,
    settings: Settings,
    adapter_runners: Mapping[str, AdapterRunner] | None,
    evaluator_snapshot: Mapping[str, Any],
    judge_secret_resolver: Callable[[str], str | None] | None,
) -> list[EvaluatorOutcome]:
    evaluator = context.evaluator
    try:
        if context.execution.status is not ExecutionStatus.COMPLETED:
            configured_metric = evaluator.config.get("metric", evaluator.name)
            key = deterministic_evaluator_key(str(configured_metric))
            if evaluator.evaluator_type is EvaluatorType.DETERMINISTIC and key in {
                "error",
                "error_rate",
                "execution_error_rate",
            }:
                return evaluate_deterministic(context)
            return [
                EvaluatorOutcome(
                    metric_name=evaluator.name,
                    status=ScoreStatus.NOT_RUN,
                    explanation="agent execution did not complete",
                )
            ]
        if evaluator.evaluator_type is EvaluatorType.DETERMINISTIC:
            configured_metric = evaluator.config.get("metric", evaluator.name)
            key = deterministic_evaluator_key(str(configured_metric))
            return evaluate_deterministic(context)
        if evaluator.evaluator_type is EvaluatorType.LLM_JUDGE:
            if is_managed_judge_snapshot(evaluator_snapshot):
                return [_pending_managed_judge_outcome(context, evaluator_snapshot)]
            connection = evaluator_snapshot.get("evaluator_connection") or {}
            auth_ref = connection.get("auth_ref") if isinstance(connection, Mapping) else None
            signing_secret = _external_judge_secret(
                settings, auth_ref, judge_secret_resolver
            )
            return await evaluate_llm_judge(
                context,
                _external_judge_config(
                    evaluator_snapshot,
                    max_retries=int(evaluator.config.get("max_retries", 2)),
                    retry_backoff_seconds=float(
                        evaluator.config.get("retry_backoff_seconds", 0.2)
                    ),
                ),
                signing_secret=signing_secret,
            )
        if evaluator.evaluator_type is EvaluatorType.ADAPTER:
            adapter = str(evaluator.config.get("adapter", "")).casefold()
            runner = adapter_runners.get(adapter) if adapter_runners is not None else None
            return await evaluate_adapter(context, runner)
        return [
            EvaluatorOutcome(
                metric_name=evaluator.name,
                status=ScoreStatus.NOT_RUN,
                explanation="human evaluator requires manual review",
            )
        ]
    except (
        EvaluatorConfigurationError,
        ExternalProtocolError,
        ThirdPartyAdapterError,
    ) as exc:
        return [_error_outcome(evaluator, exc, evaluator_snapshot)]
    except Exception as exc:  # pragma: no cover - defensive evaluator boundary
        return [_error_outcome(evaluator, exc, evaluator_snapshot)]


def _safe(value: Any, settings: Settings) -> Any:
    return sanitize_value(value, settings, PrivacyStats())


def _safe_text(value: str | None, settings: Settings) -> str | None:
    if value is None:
        return None
    sanitized = _safe(value, settings)
    return sanitized if isinstance(sanitized, str) else json.dumps(sanitized, ensure_ascii=False)


def _score(
    run: EvaluationRunRecord,
    execution: CaseExecutionRecord,
    evaluator: EvaluatorVersion,
    outcome: EvaluatorOutcome,
    settings: Settings,
    *,
    experiment_item_id: str | None = None,
    repetition: int = 1,
    attempt: int | None = None,
) -> Score:
    evidence = [_safe(item, settings) for item in outcome.evidence]
    provenance = (
        _safe(outcome.provenance.model_dump(mode="json"), settings)
        if outcome.provenance is not None
        else None
    )
    judge_model = (
        outcome.provenance.model
        if outcome.provenance is not None
        else None
    )
    source = {
        EvaluatorType.DETERMINISTIC: ScoreSource.DETERMINISTIC,
        EvaluatorType.LLM_JUDGE: ScoreSource.LLM_JUDGE,
        EvaluatorType.ADAPTER: ScoreSource.ADAPTER,
        EvaluatorType.HUMAN: ScoreSource.AUTOMATED,
    }[evaluator.evaluator_type]
    return Score(
        id=new_id(),
        run_id=run.id,
        case_id=execution.dataset_case.case_key,
        experiment_item_id=experiment_item_id,
        repetition=repetition,
        attempt=attempt,
        evaluator_version_id=evaluator.id,
        trace_id=execution.trace_id,
        source=source,
        metric_name=outcome.metric_name,
        status=outcome.status,
        value=outcome.value,
        label=_safe_text(outcome.label, settings),
        passed=outcome.passed,
        explanation=_safe_text(outcome.explanation, settings),
        evidence=evidence,
        rubric=_safe_text(evaluator.rubric, settings),
        judge_model=_safe_text(judge_model, settings),
        provenance=provenance,
        threshold=evaluator.default_threshold,
        direction=evaluator.direction,
        raw_response=_safe(outcome.raw_response, settings),
        raw_result=_safe(outcome.raw_result, settings),
    )


def _record(score: Score) -> ScoreRecord:
    return ScoreRecord(
        id=score.id,
        run_id=score.run_id,
        case_id=score.case_id,
        experiment_item_id=score.experiment_item_id,
        repetition=score.repetition,
        attempt=score.attempt,
        evaluator_version_id=score.evaluator_version_id,
        trace_id=score.trace_id,
        source=score.source.value,
        metric_name=score.metric_name,
        status=score.status.value,
        value=score.value,
        label=score.label,
        passed=score.passed,
        explanation=score.explanation,
        evidence=score.evidence,
        rubric=score.rubric,
        judge_model=score.judge_model,
        provenance=score.provenance.model_dump(mode="json") if score.provenance else None,
        threshold=score.threshold,
        direction=score.direction.value,
        raw_response=score.raw_response,
        raw_result=score.raw_result,
    )


def _add_evaluator_span(
    db: Session,
    *,
    execution: CaseExecutionRecord,
    evaluator: EvaluatorVersion,
    outcome: EvaluatorOutcome,
    score: Score,
    parent_span_id: str | None,
    started_at: datetime,
    ended_at: datetime,
) -> None:
    db.add(
        TraceSpanRecord(
            trace_id=execution.trace_id,
            span_id=new_id(),
            parent_span_id=parent_span_id,
            kind=TraceSpanKind.EVALUATOR.value,
            name=outcome.metric_name,
            status=(
                ExecutionStatus.FAILED.value
                if outcome.status is ScoreStatus.ERROR
                else ExecutionStatus.COMPLETED.value
            ),
            started_at=started_at,
            ended_at=ended_at,
            input={"evaluator_version_id": evaluator.id},
            output={
                "score_id": score.id,
                "status": score.status.value,
                "value": score.value,
                "passed": score.passed,
            },
            error=(
                {"type": "evaluation_error", "message": score.explanation}
                if score.status is ScoreStatus.ERROR
                else None
            ),
            usage=outcome.usage,
            cost=outcome.cost,
            attributes={
                "evaluator.version": evaluator.version,
                "evaluator.type": evaluator.evaluator_type.value,
            },
            extensions={},
        )
    )


def build_evaluation_context(
    execution: CaseExecutionRecord,
    trace: Trace,
    evaluator_snapshot: Mapping[str, Any],
) -> EvaluationContext:
    return EvaluationContext(
        case=_case(execution),
        execution=_execution(execution),
        evaluator=_evaluator(evaluator_snapshot),
        trace=trace,
    )


def attempt_execution_record(
    item: Any,
) -> CaseExecutionRecord:
    """Build an unpersisted projection so repeated Items cannot overwrite each other."""

    trace = item.trace
    execution = CaseExecutionRecord(
        id=f"managed-judge-{item.id}",
        run_id=item.experiment_id,
        case_id=item.case_id,
        status=item.status,
        attempt=item.attempt,
        output=item.output,
        tool_calls=[
            {
                "name": span.name,
                "arguments": span.input if isinstance(span.input, dict) else {},
                "order": (span.attributes or {}).get("tool.order"),
            }
            for span in (trace.spans if trace is not None else [])
            if span.kind == TraceSpanKind.TOOL.value
        ],
        usage=item.usage,
        error_type=item.error_type,
        error_message=item.error_message,
        trace_id=item.trace_id,
        started_at=item.started_at,
        finished_at=item.finished_at,
    )
    set_committed_value(  # type: ignore[no-untyped-call]
        execution, "dataset_case", item.dataset_case
    )
    return execution


def replace_pending_managed_score(
    db: Session,
    *,
    run: EvaluationRunRecord,
    execution: CaseExecutionRecord,
    trace: Trace,
    evaluator_snapshot: Mapping[str, Any],
    score_record: ScoreRecord,
    outcome: EvaluatorOutcome,
    settings: Settings,
    started_at: datetime,
    ended_at: datetime,
) -> None:
    """Atomically replace one queued placeholder with its final Judge result."""

    if score_record.status != ScoreStatus.NOT_RUN.value:
        return
    context = build_evaluation_context(execution, trace, evaluator_snapshot)
    score = _score(
        run,
        execution,
        context.evaluator,
        outcome,
        settings,
        experiment_item_id=score_record.experiment_item_id,
        repetition=score_record.repetition,
        attempt=score_record.attempt,
    )
    score.id = score_record.id
    score_record.status = score.status.value
    score_record.value = score.value
    score_record.label = score.label
    score_record.passed = score.passed
    score_record.explanation = score.explanation
    score_record.evidence = score.evidence
    score_record.rubric = score.rubric
    score_record.judge_model = score.judge_model
    score_record.provenance = score.provenance.model_dump(mode="json") if score.provenance else None
    score_record.threshold = score.threshold
    score_record.direction = score.direction.value
    score_record.raw_response = score.raw_response
    score_record.raw_result = score.raw_result
    parent_span_id = next(
        (span.span_id for span in trace.spans if span.parent_span_id is None),
        None,
    )
    _add_evaluator_span(
        db,
        execution=execution,
        evaluator=context.evaluator,
        outcome=outcome,
        score=score,
        parent_span_id=parent_span_id,
        started_at=started_at,
        ended_at=ended_at,
    )


async def evaluate_and_persist_scores(
    db: Session,
    run: EvaluationRunRecord,
    execution: CaseExecutionRecord,
    trace: Trace,
    settings: Settings,
    *,
    adapter_runners: Mapping[str, AdapterRunner] | None = None,
    judge_secret_resolver: Callable[[str], str | None] | None = None,
    experiment_item_id: str | None = None,
    repetition: int = 1,
    attempt: int | None = None,
) -> list[ScoreRecord]:
    """Evaluate all frozen evaluator versions once and stage Score rows for commit."""

    evaluator_snapshots = run.configuration_snapshot["evaluators"]
    persisted: list[ScoreRecord] = []
    parent_span_id = next(
        (span.span_id for span in trace.spans if span.parent_span_id is None),
        None,
    )
    existing_evaluator_ids = {
        row.evaluator_version_id
        for row in db.query(ScoreRecord)
        .filter(
            ScoreRecord.run_id == run.id,
            ScoreRecord.case_id == execution.dataset_case.case_key,
            ScoreRecord.repetition == repetition,
        )
        .all()
    }
    for evaluator_snapshot in evaluator_snapshots:
        if str(evaluator_snapshot["id"]) in existing_evaluator_ids:
            continue
        evaluator = _evaluator(evaluator_snapshot)
        context = build_evaluation_context(execution, trace, evaluator_snapshot)
        started_at = datetime.now(UTC)
        outcomes = await _evaluate(
            context,
            settings,
            adapter_runners,
            evaluator_snapshot,
            judge_secret_resolver,
        )
        ended_at = datetime.now(UTC)
        for outcome in outcomes:
            score = _score(
                run,
                execution,
                evaluator,
                outcome,
                settings,
                experiment_item_id=experiment_item_id,
                repetition=repetition,
                attempt=attempt,
            )
            row = _record(score)
            db.add(row)
            persisted.append(row)
            if not (
                outcome.status is ScoreStatus.NOT_RUN
                and isinstance(outcome.raw_result, Mapping)
                and outcome.raw_result.get("state") == "pending"
            ):
                _add_evaluator_span(
                    db,
                    execution=execution,
                    evaluator=evaluator,
                    outcome=outcome,
                    score=score,
                    parent_span_id=parent_span_id,
                    started_at=started_at,
                    ended_at=ended_at,
                )
    db.flush()
    return persisted
