"""Canonical trace persistence and project-scoped retrieval."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from agent_eval_api.auth import AuthContext, get_db, require_project_access
from agent_eval_api.contracts import (
    ExecutionStatus,
    ExternalScoreProvenance,
    OnlineScoreRequest,
    Score,
    ScoreDirection,
    ScoreSource,
    ScoreStatus,
    Trace,
    TraceIngestRequest,
    TraceSpan,
    TraceSpanKind,
    TraceSummary,
    TraceSummaryPage,
    TraceTimeline,
    TraceTimelineSpan,
)
from agent_eval_api.db import (
    DatasetCaseRecord,
    EvaluationRunRecord,
    EvaluatorVersionRecord,
    ExperimentItemAttemptRecord,
    ScoreRecord,
    TraceRecord,
    TraceSpanRecord,
    new_id,
)
from agent_eval_api.settings import Settings, get_settings
from agent_eval_api.trace_normalization import normalize_trace_payload
from agent_eval_api.trace_privacy import PrivacyStats, sanitize_trace, sanitize_value

router = APIRouter(prefix="/projects/{project_id}/traces", tags=["traces"])


def _experiment_root_attributes(trace: Trace) -> dict[str, Any] | None:
    roots = [span for span in trace.spans if span.parent_span_id is None]
    for root in roots:
        attributes = root.attributes or {}
        if "agent_eval.experiment.id" in attributes:
            return attributes
    return None


def _validate_sdk_experiment_trace(
    db: Session,
    project_id: str,
    trace: Trace,
    run: EvaluationRunRecord | None,
) -> ExperimentItemAttemptRecord | None:
    attributes = _experiment_root_attributes(trace)
    if trace.source != "sdk" and attributes is None:
        return None
    if run is None or attributes is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="SDK Trace requires a valid Experiment and root attributes",
        )
    required = {
        "agent_eval.project.id": project_id,
        "agent_eval.experiment.id": run.id,
        "agent_eval.dataset.version.id": run.dataset_version_id,
        "agent_eval.case.id": trace.case_id,
    }
    mismatched = [key for key, expected in required.items() if attributes.get(key) != expected]
    if mismatched:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "SDK Trace identity mismatch", "fields": mismatched},
        )
    item_id = attributes.get("agent_eval.experiment.item.id")
    item = db.scalar(
        select(ExperimentItemAttemptRecord)
        .join(DatasetCaseRecord, ExperimentItemAttemptRecord.case_id == DatasetCaseRecord.id)
        .where(
            ExperimentItemAttemptRecord.id == item_id,
            ExperimentItemAttemptRecord.experiment_id == run.id,
            DatasetCaseRecord.case_key == trace.case_id,
        )
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="SDK Trace does not identify an Item in this Experiment manifest",
        )
    expected_release = (run.configuration_snapshot.get("agent_version") or {}).get(
        "release_identity"
    )
    if attributes.get("agent_eval.agent.release") != expected_release:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="SDK Trace Agent Release does not match the Experiment snapshot",
        )
    return item


_OTEL_EXPERIMENT_ATTRIBUTES = {
    "agent_eval.project.id",
    "agent_eval.experiment.id",
    "agent_eval.experiment.item.id",
    "agent_eval.dataset.id",
    "agent_eval.dataset.version.id",
    "agent_eval.case.id",
    "agent_eval.agent.release",
    "agent_eval.execution.origin",
    "agent_eval.repetition",
}


def _validate_otel_experiment_trace(
    db: Session,
    project_id: str,
    trace: Trace,
    attributes: dict[str, Any],
    run: EvaluationRunRecord,
    *,
    execution_origin: str = "otel",
    protocol_name: str = "OTel",
) -> ExperimentItemAttemptRecord:
    missing = sorted(key for key in _OTEL_EXPERIMENT_ATTRIBUTES if key not in attributes)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": f"{protocol_name} Experiment Trace is missing root attributes",
                "fields": missing,
            },
        )

    dataset_snapshot = run.configuration_snapshot.get("dataset_version") or {}
    required = {
        "agent_eval.project.id": project_id,
        "agent_eval.experiment.id": run.id,
        "agent_eval.dataset.id": dataset_snapshot.get("dataset_id"),
        "agent_eval.dataset.version.id": run.dataset_version_id,
        "agent_eval.execution.origin": execution_origin,
    }
    mismatched = [key for key, expected in required.items() if attributes.get(key) != expected]
    if trace.run_id is not None and trace.run_id != run.id:
        mismatched.append("run_id")
    if trace.case_id is not None and trace.case_id != attributes["agent_eval.case.id"]:
        mismatched.append("case_id")
    if mismatched:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": f"{protocol_name} Experiment Trace identity mismatch",
                "fields": mismatched,
            },
        )

    item = db.scalar(
        select(ExperimentItemAttemptRecord)
        .join(DatasetCaseRecord, ExperimentItemAttemptRecord.case_id == DatasetCaseRecord.id)
        .where(
            ExperimentItemAttemptRecord.id == attributes["agent_eval.experiment.item.id"],
            ExperimentItemAttemptRecord.experiment_id == run.id,
            DatasetCaseRecord.case_key == attributes["agent_eval.case.id"],
        )
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{protocol_name} Trace does not identify an Item in this Experiment manifest",
        )
    expected_release = (run.configuration_snapshot.get("agent_version") or {}).get(
        "release_identity"
    )
    required_item_values = {
        "agent_eval.agent.release": expected_release,
        "agent_eval.repetition": item.repetition,
    }
    item_mismatches = [
        key for key, expected in required_item_values.items() if attributes.get(key) != expected
    ]
    if item_mismatches:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": f"{protocol_name} Trace Item identity mismatch",
                "fields": item_mismatches,
            },
        )

    trace.run_id = run.id
    trace.case_id = item.dataset_case.case_key
    return item


def _validate_experiment_trace(
    db: Session,
    project_id: str,
    trace: Trace,
    run: EvaluationRunRecord | None,
) -> ExperimentItemAttemptRecord | None:
    attributes = _experiment_root_attributes(trace)
    if attributes is None:
        return _validate_sdk_experiment_trace(db, project_id, trace, run)

    attribute_run_id = attributes.get("agent_eval.experiment.id")
    if not isinstance(attribute_run_id, str) or not attribute_run_id:
        return _validate_sdk_experiment_trace(db, project_id, trace, run)
    if run is None:
        run = db.scalar(
            select(EvaluationRunRecord).where(
                EvaluationRunRecord.id == attribute_run_id,
                EvaluationRunRecord.project_id == project_id,
            )
        )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="evaluation run not found"
        )
    if run.execution_mode == "otel":
        return _validate_otel_experiment_trace(db, project_id, trace, attributes, run)
    if run.execution_mode == "remote_upload":
        return _validate_otel_experiment_trace(
            db,
            project_id,
            trace,
            attributes,
            run,
            execution_origin="remote_upload",
            protocol_name="Remote upload",
        )
    return _validate_sdk_experiment_trace(db, project_id, trace, run)


def _attach_trace_to_item(
    item: ExperimentItemAttemptRecord | None,
    record: TraceRecord,
) -> None:
    if item is None:
        return
    if item.source_trace_id is not None and item.source_trace_id != record.trace_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Item already references a different public Trace ID",
        )
    item.source_trace_id = record.trace_id
    item.trace_id = record.id


def get_project_trace(db: Session, project_id: str, trace_id: str) -> TraceRecord:
    trace = db.scalar(
        select(TraceRecord)
        .where(
            TraceRecord.project_id == project_id,
            or_(
                TraceRecord.trace_id == trace_id,
                TraceRecord.trace_id.is_(None) & (TraceRecord.id == trace_id),
            ),
        )
        .options(selectinload(TraceRecord.spans))
        .options(selectinload(TraceRecord.scores))
        .options(selectinload(TraceRecord.scores))
        .order_by(TraceRecord.created_at.desc(), TraceRecord.id.desc())
    )
    if trace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="trace not found")
    return trace


def trace_response(trace: TraceRecord, settings: Settings | None = None) -> Trace:
    public_trace_id = trace.trace_id or trace.id
    response = Trace(
        trace_id=public_trace_id,
        run_id=trace.run_id,
        case_id=trace.case_id,
        status=ExecutionStatus(trace.status),
        spans=[
            TraceSpan(
                span_id=span.span_id,
                trace_id=public_trace_id,
                parent_span_id=span.parent_span_id,
                kind=TraceSpanKind(span.kind),
                name=span.name,
                status=ExecutionStatus(span.status),
                started_at=span.started_at,
                ended_at=span.ended_at,
                input=span.input,
                output=span.output,
                error=span.error,
                usage=span.usage or {},
                cost=span.cost,
                attributes=span.attributes,
                extensions=span.extensions,
            )
            for span in sorted(trace.spans, key=lambda item: (item.started_at, item.span_id))
        ],
        scores=[score_response(score, public_trace_id) for score in trace.scores],
        source=trace.source,
        extensions=trace.extensions,
    )
    return sanitize_trace(response, settings) if settings is not None else response


def score_response(record: ScoreRecord, public_trace_id: str | None = None) -> Score:
    return Score(
        id=record.id,
        run_id=record.run_id,
        case_id=record.case_id,
        experiment_item_id=record.experiment_item_id,
        repetition=record.repetition,
        attempt=record.attempt,
        metric_name=record.metric_name,
        evaluator_version_id=record.evaluator_version_id,
        trace_id=public_trace_id or record.trace_id,
        span_id=record.span_id,
        source=ScoreSource(record.source),
        status=ScoreStatus(record.status),
        value=record.value,
        label=record.label,
        passed=record.passed,
        explanation=record.explanation,
        evidence=record.evidence,
        rubric=record.rubric,
        judge_model=record.judge_model,
        provenance=(
            ExternalScoreProvenance.model_validate(record.provenance)
            if record.provenance is not None
            else None
        ),
        threshold=record.threshold,
        direction=ScoreDirection(record.direction),
        raw_response=record.raw_response,
        raw_result=record.raw_result,
    )


def trace_summary(trace: TraceRecord) -> TraceSummary:
    spans = sorted(trace.spans, key=lambda item: (item.started_at, item.span_id))
    started_at = spans[0].started_at if spans else None
    ended_at = max(
        (span.ended_at or span.started_at for span in spans),
        default=None,
    )
    return TraceSummary(
        trace_id=trace.trace_id or trace.id,
        run_id=trace.run_id,
        case_id=trace.case_id,
        status=ExecutionStatus(trace.status),
        source=trace.source,
        span_count=len(spans),
        started_at=started_at,
        ended_at=ended_at,
        created_at=trace.created_at,
    )


def trace_timeline(trace: TraceRecord) -> TraceTimeline:
    ordered_spans = sorted(trace.spans, key=lambda item: (item.started_at, item.span_id))
    parent_by_span = {span.span_id: span.parent_span_id for span in ordered_spans}

    def depth(span_id: str) -> int:
        result = 0
        parent_span_id = parent_by_span[span_id]
        while parent_span_id is not None:
            result += 1
            parent_span_id = parent_by_span[parent_span_id]
        return result

    return TraceTimeline(
        trace_id=trace.trace_id or trace.id,
        started_at=ordered_spans[0].started_at if ordered_spans else None,
        ended_at=max(
            (span.ended_at or span.started_at for span in ordered_spans),
            default=None,
        ),
        spans=[
            TraceTimelineSpan(
                span_id=span.span_id,
                parent_span_id=span.parent_span_id,
                kind=TraceSpanKind(span.kind),
                name=span.name,
                status=ExecutionStatus(span.status),
                started_at=span.started_at,
                ended_at=span.ended_at,
                duration_ms=(
                    (span.ended_at - span.started_at).total_seconds() * 1000
                    if span.ended_at is not None
                    else None
                ),
                depth=depth(span.span_id),
            )
            for span in ordered_spans
        ],
    )


def persist_trace(
    db: Session,
    project_id: str,
    trace: Trace,
    settings: Settings,
    *,
    commit: bool = True,
) -> TraceRecord:
    trace = sanitize_trace(trace, settings)
    if len(trace.spans) > settings.trace_max_spans:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="trace span count exceeds configured limit",
        )
    _validate_nesting_depth(trace, settings.trace_max_nesting_depth)

    run = None
    if trace.run_id is not None:
        run = db.scalar(
            select(EvaluationRunRecord).where(
                EvaluationRunRecord.id == trace.run_id,
                EvaluationRunRecord.project_id == project_id,
            )
        )
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="evaluation run not found"
            )
    experiment_item = _validate_experiment_trace(db, project_id, trace, run)

    existing = db.scalar(
        select(TraceRecord)
        .where(
            TraceRecord.project_id == project_id,
            TraceRecord.source == trace.source,
            or_(TraceRecord.trace_id == trace.trace_id, TraceRecord.id == trace.trace_id),
        )
        .options(selectinload(TraceRecord.spans))
    )
    if existing is not None:
        if _trace_fingerprint(trace, settings) == _trace_fingerprint(
            trace_response(existing, settings), settings
        ):
            _attach_trace_to_item(experiment_item, existing)
            if commit:
                db.commit()
            return existing
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="trace id already exists with different content",
        )
    record = TraceRecord(
        project_id=project_id,
        trace_id=trace.trace_id,
        run_id=trace.run_id,
        case_id=trace.case_id,
        status=trace.status.value,
        source=trace.source,
        extensions=trace.extensions,
    )
    record.spans = [
        TraceSpanRecord(
            trace_id=record.id,
            span_id=span.span_id,
            parent_span_id=span.parent_span_id,
            kind=span.kind.value,
            name=span.name,
            status=span.status.value,
            started_at=span.started_at,
            ended_at=span.ended_at,
            input=span.input,
            output=span.output,
            error=span.error,
            usage=span.usage,
            cost=span.cost,
            attributes=span.attributes,
            extensions=span.extensions,
        )
        for span in trace.spans
    ]
    db.add(record)
    db.flush()
    _attach_trace_to_item(experiment_item, record)
    if commit:
        db.commit()
        db.refresh(record)
    else:
        db.flush()
    return record


def _trace_fingerprint(trace: Trace, settings: Settings) -> str:
    """Fingerprint the public, sanitized representation for retry detection."""

    payload = trace.model_dump(mode="json")
    # Scores are derived observations, not part of the ingested Trace payload.
    payload.pop("scores", None)
    # Privacy counters are generated by the platform while sanitizing. They
    # describe storage/presentation work, rather than Agent content, so they
    # must not make an otherwise identical retry look like a conflict.
    _remove_privacy_metadata(payload)
    _normalize_timestamp_metadata(payload)
    spans = payload.get("spans")
    if isinstance(spans, list):
        payload["spans"] = sorted(
            spans,
            key=lambda span: str(span.get("span_id"))
            if isinstance(span, dict)
            else str(span),
        )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _remove_privacy_metadata(payload: dict[str, Any]) -> None:
    extensions = payload.get("extensions")
    if isinstance(extensions, dict):
        extensions.pop("agent_eval.privacy", None)
    spans = payload.get("spans")
    if isinstance(spans, list):
        for span in spans:
            if isinstance(span, dict) and isinstance(span.get("extensions"), dict):
                span["extensions"].pop("agent_eval.privacy", None)


def _normalize_timestamp_metadata(payload: dict[str, Any]) -> None:
    """Make timezone-aware request timestamps comparable with DB values."""

    spans = payload.get("spans")
    if not isinstance(spans, list):
        return
    for span in spans:
        if not isinstance(span, dict):
            continue
        for field_name in ("started_at", "ended_at"):
            value = span.get(field_name)
            if not isinstance(value, str):
                continue
            try:
                timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            if timestamp.tzinfo is not None:
                timestamp = timestamp.astimezone(UTC).replace(tzinfo=None)
            span[field_name] = timestamp.isoformat(timespec="microseconds")


def _validate_nesting_depth(trace: Trace, max_depth: int) -> None:
    parent_by_span = {span.span_id: span.parent_span_id for span in trace.spans}
    for span in trace.spans:
        depth = 0
        parent_span_id = span.parent_span_id
        while parent_span_id is not None:
            depth += 1
            if depth > max_depth:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="trace nesting depth exceeds configured limit",
                )
            parent_span_id = parent_by_span[parent_span_id]


@router.post("", response_model=Trace, status_code=status.HTTP_201_CREATED)
def create_trace(
    project_id: str,
    payload: Trace,
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> Trace:
    return trace_response(persist_trace(db, project_id, payload, settings))


@router.post("/ingest", response_model=Trace, status_code=status.HTTP_201_CREATED)
def ingest_trace(
    project_id: str,
    payload: TraceIngestRequest,
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> Trace:
    trace = payload.trace or normalize_trace_payload(payload.payload or {}, source=payload.source)
    return trace_response(persist_trace(db, project_id, trace, settings))


@router.post("/otlp", response_model=Trace, status_code=status.HTTP_201_CREATED)
def ingest_otlp_trace(
    project_id: str,
    payload: dict[str, Any],
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> Trace:
    """Ingest an OTLP HTTP JSON ExportTraceServiceRequest."""

    _validate_otlp_http_payload(payload)
    trace = normalize_trace_payload(payload, source="otlp")
    return trace_response(persist_trace(db, project_id, trace, settings))


@router.post("/{trace_id}/scores", response_model=Score, status_code=status.HTTP_201_CREATED)
def create_online_score(
    project_id: str,
    trace_id: str,
    payload: OnlineScoreRequest,
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> Score:
    """Persist one score for a production Trace or one of its Spans."""

    trace = get_project_trace(db, project_id, trace_id)
    evaluator = db.scalar(
        select(EvaluatorVersionRecord).where(
            EvaluatorVersionRecord.id == payload.evaluator_version_id,
            EvaluatorVersionRecord.project_id == project_id,
        )
    )
    if evaluator is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="evaluator not found")

    if payload.span_id is not None:
        span = db.scalar(
            select(TraceSpanRecord).where(
                TraceSpanRecord.trace_id == trace.id,
                TraceSpanRecord.span_id == payload.span_id,
            )
        )
        if span is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="span not found")

    if payload.value is not None:
        if evaluator.score_min is not None and payload.value < evaluator.score_min:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="value is below evaluator score_min",
            )
        if evaluator.score_max is not None and payload.value > evaluator.score_max:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="value is above evaluator score_max",
            )

    privacy = PrivacyStats()
    record = ScoreRecord(
        id=new_id(),
        run_id=trace.run_id,
        case_id=trace.case_id,
        metric_name=evaluator.name,
        evaluator_version_id=evaluator.id,
        trace_id=trace.id,
        span_id=payload.span_id,
        source=payload.source,
        status=payload.status.value,
        value=payload.value,
        label=sanitize_value(payload.label, settings, privacy),
        passed=payload.passed,
        explanation=sanitize_value(payload.explanation, settings, privacy),
        evidence=[sanitize_value(item, settings, privacy) for item in payload.evidence],
        rubric=evaluator.rubric,
        judge_model=sanitize_value(
            payload.judge_model or (payload.provenance.model if payload.provenance else None),
            settings,
            privacy,
        ),
        provenance=sanitize_value(
            payload.provenance.model_dump(mode="json") if payload.provenance else None,
            settings,
            privacy,
        ),
        threshold=evaluator.default_threshold,
        direction=evaluator.direction,
        raw_response=sanitize_value(payload.raw_response, settings, privacy),
        raw_result=sanitize_value(payload.raw_result, settings, privacy),
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "uq_scores_case_metric_source" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="score already exists for this run case and source",
            ) from None
        raise
    db.refresh(record)
    return score_response(record, trace.trace_id or trace.id)


def _validate_otlp_http_payload(payload: dict[str, Any]) -> None:
    """Reject malformed OTLP envelopes before normalization or persistence."""

    resource_spans = payload.get("resourceSpans")
    if not isinstance(resource_spans, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="OTLP HTTP JSON requires a resourceSpans array",
        )

    for resource_index, resource_span in enumerate(resource_spans):
        if not isinstance(resource_span, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"resourceSpans[{resource_index}] must be an object",
            )
        scope_spans = resource_span.get("scopeSpans", [])
        if not isinstance(scope_spans, list):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"resourceSpans[{resource_index}].scopeSpans must be an array",
            )
        for scope_index, scope_span in enumerate(scope_spans):
            if not isinstance(scope_span, dict):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"resourceSpans[{resource_index}].scopeSpans[{scope_index}] "
                        "must be an object"
                    ),
                )
            spans = scope_span.get("spans", [])
            if not isinstance(spans, list):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"resourceSpans[{resource_index}].scopeSpans[{scope_index}]"
                        ".spans must be an array"
                    ),
                )
            for span_index, span in enumerate(spans):
                if not isinstance(span, dict):
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=(
                            f"resourceSpans[{resource_index}].scopeSpans[{scope_index}]"
                            f".spans[{span_index}] must be an object"
                        ),
                    )


@router.get("", response_model=TraceSummaryPage)
def list_traces(
    project_id: str,
    run_id: str | None = None,
    case_id: str | None = None,
    query: str | None = Query(default=None, min_length=1, max_length=200),  # noqa: B008
    trace_status: ExecutionStatus | None = Query(  # noqa: B008
        default=None,
        alias="status",
    ),
    offset: int = Query(default=0, ge=0),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=200),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> TraceSummaryPage:
    filters = [TraceRecord.project_id == project_id]
    if run_id is not None:
        filters.append(TraceRecord.run_id == run_id)
    if case_id is not None:
        filters.append(TraceRecord.case_id == case_id)
    if trace_status is not None:
        filters.append(TraceRecord.status == trace_status.value)
    if query is not None:
        pattern = f"%{query.strip()}%"
        filters.append(
            or_(
                TraceRecord.trace_id.ilike(pattern),
                TraceRecord.run_id.ilike(pattern),
                TraceRecord.case_id.ilike(pattern),
                TraceRecord.source.ilike(pattern),
            )
        )

    total = db.scalar(select(func.count()).select_from(TraceRecord).where(*filters)) or 0
    records = db.scalars(
        select(TraceRecord)
        .where(*filters)
        .options(selectinload(TraceRecord.spans))
        .order_by(TraceRecord.created_at.desc(), TraceRecord.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    next_offset = offset + len(records) if offset + len(records) < total else None
    return TraceSummaryPage(
        items=[trace_summary(trace) for trace in records],
        total=total,
        offset=offset,
        limit=limit,
        next_offset=next_offset,
    )


@router.get("/{trace_id}/timeline", response_model=TraceTimeline)
def read_trace_timeline(
    project_id: str,
    trace_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> TraceTimeline:
    return trace_timeline(get_project_trace(db, project_id, trace_id))


@router.get("/{trace_id}", response_model=Trace)
def read_trace(
    project_id: str,
    trace_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> Trace:
    return trace_response(get_project_trace(db, project_id, trace_id), settings)
