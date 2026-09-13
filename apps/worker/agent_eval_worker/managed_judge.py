"""Durable execution of platform-managed LLM Judge scores."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_eval_api.contracts import ExternalScoreProvenance, ScoreStatus
from agent_eval_api.credential_encryption import (
    CredentialCipher,
    CredentialEncryptionError,
)
from agent_eval_api.db import (
    EvaluationRunRecord,
    ExperimentItemAttemptRecord,
    ProviderConnectionRecord,
    ScoreRecord,
)
from agent_eval_api.evaluation import (
    ManagedJudgeConfig,
    ManagedJudgeError,
    ManagedJudgeRetryableError,
    aggregate_run_scores,
    attempt_execution_record,
    build_evaluation_context,
    evaluate_managed_judge,
    replace_pending_managed_score,
)
from agent_eval_api.evaluation.base import EvaluatorOutcome
from agent_eval_api.run_state import transition_run
from agent_eval_api.settings import Settings
from agent_eval_api.traces import trace_response


def _snapshot(run: EvaluationRunRecord, evaluator_id: str) -> Mapping[str, Any]:
    for snapshot in run.configuration_snapshot.get("evaluators", []):
        if str(snapshot.get("id")) == evaluator_id:
            return cast(Mapping[str, Any], snapshot)
    raise ManagedJudgeError("managed Judge evaluator snapshot is missing")


def _claim_score(db: Session, score_id: str, owner_id: str) -> ScoreRecord | None:
    score = db.scalar(
        select(ScoreRecord).where(ScoreRecord.id == score_id).with_for_update()
    )
    if score is None or score.status != ScoreStatus.NOT_RUN.value:
        return None
    raw = score.raw_result if isinstance(score.raw_result, dict) else {}
    current_owner = raw.get("owner_id")
    if raw.get("state") == "running" and current_owner != owner_id:
        return None
    score.raw_result = {**raw, "state": "running", "owner_id": owner_id}
    score.explanation = "managed Judge is running"
    db.commit()
    return score


def _retry_pending(db: Session, score: ScoreRecord, owner_id: str) -> None:
    raw = score.raw_result if isinstance(score.raw_result, dict) else {}
    if raw.get("owner_id") != owner_id:
        return
    score.raw_result = {
        key: value
        for key, value in raw.items()
        if key not in {"owner_id", "task_id"}
    } | {"state": "queued"}
    score.explanation = "managed Judge is waiting for retry"
    db.commit()


def _error_outcome(snapshot: Mapping[str, Any], exc: Exception) -> EvaluatorOutcome:
    name = str(snapshot["name"])
    version = str(snapshot.get("version") or "unknown")
    provider = snapshot.get("provider_connection")
    provider_mapping = provider if isinstance(provider, Mapping) else {}
    model = str(snapshot.get("judge_model") or provider_mapping.get("model") or "")
    return EvaluatorOutcome(
        metric_name=name,
        status=ScoreStatus.ERROR,
        passed=None,
        explanation=str(exc),
        provenance=ExternalScoreProvenance(
            source="platform_provider",
            protocol="openai_compatible_chat_completions",
            connection_id=str(provider_mapping.get("id") or "") or None,
            evaluator_version=f"{name}@{version}",
            model=model or None,
            rubric_version=version,
            prompt_template_version=version,
            metadata={
                "state": "error",
                "provider_connection_id": str(provider_mapping.get("id") or ""),
            },
        ),
        raw_result={
            "protocol": "openai_compatible_chat_completions",
            "provider": "platform_provider",
            "state": "error",
            "error_type": getattr(exc, "error_type", "managed_judge_error"),
            "message": str(exc),
        },
    )


def _finish_run_if_ready(db: Session, run: EvaluationRunRecord) -> None:
    # Judge workers finish in separate transactions. Serialize the readiness
    # check and aggregate rebuild on the run row so only one worker can
    # rebuild a run at a time on PostgreSQL.
    locked_run = db.scalar(
        select(EvaluationRunRecord)
        .where(EvaluationRunRecord.id == run.id)
        .with_for_update()
    )
    if locked_run is None or locked_run.status != "running":
        return
    scores = list(
        db.scalars(
            select(ScoreRecord).where(ScoreRecord.run_id == locked_run.id)
        ).all()
    )
    managed = [
        score
        for score in scores
        if isinstance(score.provenance, dict)
        and score.provenance.get("source") == "platform_provider"
    ]
    if any(score.status == ScoreStatus.NOT_RUN.value for score in managed):
        return
    aggregate_run_scores(db, locked_run)
    managed_incomplete = any(
        score.status in {
            ScoreStatus.ERROR.value,
            ScoreStatus.MISSING.value,
            ScoreStatus.NOT_RUN.value,
        }
        for score in managed
    )
    if managed_incomplete:
        target = "partial"
    elif locked_run.failed_cases == 0:
        target = "completed"
    elif locked_run.completed_cases == 0:
        target = "failed"
    else:
        target = "partial"
    from agent_eval_api.contracts import RunStatus

    transition_run(locked_run, RunStatus(target))


def execute_managed_judge(
    db: Session,
    settings: Settings,
    *,
    score_id: str,
    owner_id: str,
) -> dict[str, Any]:
    """Execute one idempotent Judge attempt or raise a retryable safe error."""

    score = _claim_score(db, score_id, owner_id)
    if score is None:
        return {"status": "ignored", "score_id": score_id}
    run = db.get(EvaluationRunRecord, score.run_id)
    item = db.get(ExperimentItemAttemptRecord, score.experiment_item_id)
    if run is None or item is None or item.trace is None:
        evidence_error = ManagedJudgeError("managed Judge evidence records are missing")
        snapshot: Mapping[str, Any] = {"name": score.metric_name}
        outcome = _error_outcome(snapshot, evidence_error)
        score.status = outcome.status.value
        score.passed = None
        score.explanation = outcome.explanation
        score.raw_result = outcome.raw_result
        if run is not None:
            _finish_run_if_ready(db, run)
        db.commit()
        return {"status": "error", "score_id": score_id}

    snapshot = _snapshot(run, score.evaluator_version_id)
    provider_snapshot = snapshot.get("provider_connection")
    exc: ManagedJudgeError | None = None
    provider_snapshot_mapping: Mapping[str, Any] | None = None
    if not isinstance(provider_snapshot, Mapping):
        exc = ManagedJudgeError("managed Judge provider snapshot is missing")
        provider = None
    else:
        provider_snapshot_mapping = cast(Mapping[str, Any], provider_snapshot)
        provider = db.scalar(
            select(ProviderConnectionRecord).where(
                ProviderConnectionRecord.id == str(provider_snapshot_mapping.get("id")),
                ProviderConnectionRecord.project_id == run.project_id,
            )
        )
        exc = (
            ManagedJudgeError("managed Judge provider credential is unavailable")
            if provider is None
            else None
        )

    trace = trace_response(item.trace, settings)
    execution = attempt_execution_record(item)
    context = build_evaluation_context(execution, trace, snapshot)
    started_at = datetime.now(UTC)
    try:
        if exc is not None or provider is None or provider_snapshot_mapping is None:
            raise exc or ManagedJudgeError("managed Judge provider is unavailable")
        try:
            api_key = CredentialCipher.from_settings(settings).decrypt(
                ciphertext=provider.credential_ciphertext,
                nonce=provider.credential_nonce,
                stored_key_id=provider.credential_key_id,
                project_id=provider.project_id,
                connection_id=provider.id,
            )
        except CredentialEncryptionError as decrypt_error:
            raise ManagedJudgeError(
                "managed Judge provider credential could not be decrypted"
            ) from decrypt_error
        sampling = snapshot.get("sampling_parameters")
        outcome = evaluate_managed_judge(
            context,
            ManagedJudgeConfig(
                base_url=str(provider_snapshot_mapping["base_url"]),
                model=str(snapshot.get("judge_model") or provider_snapshot_mapping["model"]),
                prompt_template=str(snapshot["prompt_template"]),
                output_schema=dict(snapshot["output_schema"]),
                sampling_parameters=dict(sampling) if isinstance(sampling, Mapping) else {},
                default_parameters=dict(provider_snapshot_mapping.get("default_parameters") or {}),
                timeout_seconds=float(
                    (snapshot.get("config") or {}).get("timeout_seconds", 60.0)
                ),
            ),
            api_key=api_key,
        )
    except ManagedJudgeRetryableError:
        _retry_pending(db, score, owner_id)
        raise
    except ManagedJudgeError as error:
        outcome = _error_outcome(snapshot, error)
    except Exception:  # pragma: no cover - defensive task boundary
        outcome = _error_outcome(
            snapshot,
            ManagedJudgeError("managed Judge execution failed unexpectedly"),
        )
    finally:
        api_key = ""  # Keep credential lifetime at the provider call boundary.

    ended_at = datetime.now(UTC)
    replace_pending_managed_score(
        db,
        run=run,
        execution=execution,
        trace=trace,
        evaluator_snapshot=snapshot,
        score_record=score,
        outcome=outcome,
        settings=settings,
        started_at=started_at,
        ended_at=ended_at,
    )
    _finish_run_if_ready(db, run)
    db.commit()
    return {"status": outcome.status.value, "score_id": score_id}


def fail_managed_judge(
    db: Session,
    settings: Settings,
    *,
    score_id: str,
    owner_id: str,
    message: str,
) -> dict[str, Any]:
    """Persist a terminal error after Celery exhausts transient retries."""

    score = _claim_score(db, score_id, owner_id)
    if score is None:
        return {"status": "ignored", "score_id": score_id}
    run = db.get(EvaluationRunRecord, score.run_id)
    item = db.get(ExperimentItemAttemptRecord, score.experiment_item_id)
    if run is None or item is None or item.trace is None:
        score.status = ScoreStatus.ERROR.value
        score.passed = None
        score.explanation = message
        score.raw_result = {
            "protocol": "openai_compatible_chat_completions",
            "provider": "platform_provider",
            "state": "error",
            "error_type": "managed_judge_retry_exhausted",
            "message": message,
        }
        if run is not None:
            _finish_run_if_ready(db, run)
        db.commit()
        return {"status": "error", "score_id": score_id}
    snapshot = _snapshot(run, score.evaluator_version_id)
    trace = trace_response(item.trace, settings)
    execution = attempt_execution_record(item)
    now = datetime.now(UTC)
    replace_pending_managed_score(
        db,
        run=run,
        execution=execution,
        trace=trace,
        evaluator_snapshot=snapshot,
        score_record=score,
        outcome=_error_outcome(snapshot, ManagedJudgeError(message)),
        settings=settings,
        started_at=now,
        ended_at=now,
    )
    _finish_run_if_ready(db, run)
    db.commit()
    return {"status": "error", "score_id": score_id}
