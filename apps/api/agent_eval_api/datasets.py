"""Versioned evaluation dataset APIs."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent_eval_api.auth import AuthContext, get_db, require_project_access
from agent_eval_api.contracts import (
    ChatMessage,
    Dataset,
    DatasetCase,
    DatasetCaseCreateRequest,
    DatasetCreateRequest,
    DatasetImportCommitRequest,
    DatasetImportCommitResponse,
    DatasetImportIssueResponse,
    DatasetImportPreviewResponse,
    DatasetImportRequest,
    DatasetUpdateRequest,
    DatasetVersion,
    DatasetVersionCreateRequest,
    ExpectedToolCall,
    RetrievalContext,
    Trace,
    TraceFieldSelection,
    TraceToDatasetCaseRequest,
)
from agent_eval_api.dataset_export import (
    export_dataset_version_csv,
    export_dataset_version_jsonl,
)
from agent_eval_api.dataset_import import (
    DatasetImportFormat,
    DatasetParseResult,
    ImportIssue,
    parse_dataset_bytes,
)
from agent_eval_api.db import (
    DatasetCaseRecord,
    DatasetRecord,
    DatasetVersionRecord,
    ProjectRecord,
    new_id,
)
from agent_eval_api.settings import Settings, get_settings
from agent_eval_api.traces import get_project_trace, trace_response

router = APIRouter(prefix="/projects/{project_id}/datasets", tags=["datasets"])


def utc_now() -> datetime:
    return datetime.now(UTC)


def get_project(db: Session, project_id: str) -> ProjectRecord:
    project = db.get(ProjectRecord, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return project


def get_dataset(db: Session, project_id: str, dataset_id: str) -> DatasetRecord:
    dataset = db.scalar(
        select(DatasetRecord).where(
            DatasetRecord.id == dataset_id,
            DatasetRecord.project_id == project_id,
        )
    )
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dataset not found")
    return dataset


def get_version(
    db: Session, project_id: str, dataset_id: str, version_id: str
) -> DatasetVersionRecord:
    get_dataset(db, project_id, dataset_id)
    version = db.scalar(
        select(DatasetVersionRecord).where(
            DatasetVersionRecord.id == version_id,
            DatasetVersionRecord.dataset_id == dataset_id,
        )
    )
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="dataset version not found"
        )
    return version


def case_record(version_id: str, case: DatasetCase) -> DatasetCaseRecord:
    return DatasetCaseRecord(
        id=new_id(),
        dataset_version_id=version_id,
        case_key=case.id,
        input_json=case.input,
        variables=case.variables,
        expected_output=case.expected_output,
        output_schema=case.output_schema,
        criteria=case.criteria,
        expected_tools=[tool.model_dump(mode="json") for tool in case.expected_tools],
        expected_state=case.expected_state,
        retrieval_context=[item.model_dump(mode="json") for item in case.retrieval_context],
        messages=[message.model_dump(mode="json") for message in case.messages],
        metadata_json=case.metadata,
        source_trace_id=case.source_trace_id,
        source_span_ids=case.source_span_ids,
        source_mapping=case.source_mapping,
    )


def case_response(case: DatasetCaseRecord) -> DatasetCase:
    return DatasetCase(
        id=case.case_key,
        input=case.input_json,
        variables=case.variables,
        expected_output=case.expected_output,
        output_schema=case.output_schema,
        criteria=case.criteria,
        expected_tools=[ExpectedToolCall.model_validate(tool) for tool in case.expected_tools],
        expected_state=case.expected_state,
        retrieval_context=[
            RetrievalContext.model_validate(item) for item in case.retrieval_context
        ],
        messages=[ChatMessage.model_validate(message) for message in case.messages],
        metadata=case.metadata_json,
        source_trace_id=case.source_trace_id,
        source_span_ids=case.source_span_ids,
        source_mapping=case.source_mapping,
    )


def version_response(version: DatasetVersionRecord) -> DatasetVersion:
    return DatasetVersion(
        id=version.id,
        dataset_id=version.dataset_id,
        version=version.version,
        cases=[
            case_response(case) for case in sorted(version.cases, key=lambda item: item.case_key)
        ],
        metadata=version.metadata_json,
        created_at=version.created_at,
    )


def dataset_response(dataset: DatasetRecord) -> Dataset:
    return Dataset(
        id=dataset.id,
        project_id=dataset.project_id,
        name=dataset.name,
        description=dataset.description,
        tags=dataset.tags,
        current_version_id=dataset.current_version_id,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
    )


def validate_unique_cases(cases: list[DatasetCase]) -> None:
    case_ids = [case.id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="dataset case ids must be unique within a version",
        )


def parse_import(payload: DatasetImportRequest) -> DatasetParseResult:
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except binascii.Error:
        return DatasetParseResult(issues=[ImportIssue(0, "content_base64 is not valid base64")])
    return parse_dataset_bytes(
        content,
        DatasetImportFormat(payload.format),
        encoding=payload.encoding,
        field_mapping=payload.field_mapping or None,
    )


def import_preview_response(parsed: DatasetParseResult) -> DatasetImportPreviewResponse:
    return DatasetImportPreviewResponse(
        cases=parsed.cases,
        issues=[
            DatasetImportIssueResponse(line=issue.line, reason=issue.reason)
            for issue in parsed.issues
        ],
    )


def create_version(
    db: Session,
    dataset: DatasetRecord,
    cases: list[DatasetCase],
    metadata: dict[str, Any],
) -> DatasetVersionRecord:
    validate_unique_cases(cases)
    latest = db.scalar(
        select(func.max(DatasetVersionRecord.version)).where(
            DatasetVersionRecord.dataset_id == dataset.id
        )
    )
    version = DatasetVersionRecord(
        id=new_id(),
        dataset_id=dataset.id,
        version=(latest or 0) + 1,
        metadata_json=metadata,
        created_at=utc_now(),
    )
    version.cases = [case_record(version.id, case) for case in cases]
    db.add(version)
    # Dataset and DatasetVersion have a circular reference through
    # current_version_id. Persist the version before pointing the dataset at it.
    db.commit()
    db.refresh(version)
    dataset.current_version_id = version.id
    dataset.updated_at = utc_now()
    db.commit()
    return version


@router.post("", response_model=Dataset, status_code=status.HTTP_201_CREATED)
def create_dataset(
    project_id: str,
    payload: DatasetCreateRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> Dataset:
    get_project(db, project_id)
    dataset = DatasetRecord(
        id=new_id(),
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        tags=payload.tags,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(dataset)
    db.flush()
    version = create_version(db, dataset, payload.cases, payload.metadata)
    dataset.current_version_id = version.id
    db.commit()
    db.refresh(dataset)
    return dataset_response(dataset)


@router.get("", response_model=list[Dataset])
def list_datasets(
    project_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> list[Dataset]:
    get_project(db, project_id)
    datasets = db.scalars(
        select(DatasetRecord)
        .where(DatasetRecord.project_id == project_id)
        .order_by(DatasetRecord.created_at)
    ).all()
    return [dataset_response(dataset) for dataset in datasets]


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(
    project_id: str,
    dataset_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> None:
    """Delete an empty dataset created while an import is being previewed."""
    dataset = get_dataset(db, project_id, dataset_id)
    if any(version.cases or version.runs for version in dataset.versions):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="dataset with cases or runs cannot be deleted",
        )
    dataset.current_version_id = None
    db.flush()
    db.delete(dataset)
    db.commit()


@router.get("/{dataset_id}", response_model=Dataset)
def read_dataset(
    project_id: str,
    dataset_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> Dataset:
    return dataset_response(get_dataset(db, project_id, dataset_id))


@router.patch("/{dataset_id}", response_model=Dataset)
def update_dataset(
    project_id: str,
    dataset_id: str,
    payload: DatasetUpdateRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> Dataset:
    dataset = get_dataset(db, project_id, dataset_id)
    if "name" in payload.model_fields_set:
        if payload.name is not None:
            dataset.name = payload.name
    if "description" in payload.model_fields_set:
        dataset.description = payload.description
    if "tags" in payload.model_fields_set:
        dataset.tags = payload.tags or []
    dataset.updated_at = utc_now()
    db.commit()
    db.refresh(dataset)
    return dataset_response(dataset)


@router.get("/{dataset_id}/versions", response_model=list[DatasetVersion])
def list_versions(
    project_id: str,
    dataset_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> list[DatasetVersion]:
    dataset = get_dataset(db, project_id, dataset_id)
    versions = db.scalars(
        select(DatasetVersionRecord)
        .where(DatasetVersionRecord.dataset_id == dataset.id)
        .order_by(DatasetVersionRecord.version)
    ).all()
    return [version_response(version) for version in versions]


@router.get("/{dataset_id}/versions/{version_id}", response_model=DatasetVersion)
def read_version(
    project_id: str,
    dataset_id: str,
    version_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> DatasetVersion:
    return version_response(get_version(db, project_id, dataset_id, version_id))


@router.get("/{dataset_id}/export")
def export_current_version(
    project_id: str,
    dataset_id: str,
    format: DatasetImportFormat = DatasetImportFormat.JSONL,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> Response:
    dataset = get_dataset(db, project_id, dataset_id)
    if dataset.current_version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="dataset version not found"
        )
    return export_version_response(dataset, dataset.current_version, format)


@router.get("/{dataset_id}/versions/{version_id}/export")
def export_version(
    project_id: str,
    dataset_id: str,
    version_id: str,
    format: DatasetImportFormat = DatasetImportFormat.JSONL,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> Response:
    dataset = get_dataset(db, project_id, dataset_id)
    version = get_version(db, project_id, dataset_id, version_id)
    return export_version_response(dataset, version, format)


def export_version_response(
    dataset: DatasetRecord,
    version: DatasetVersionRecord,
    format: DatasetImportFormat,
) -> Response:
    dataset_model = dataset_response(dataset)
    version_model = version_response(version)
    if format is DatasetImportFormat.JSONL:
        content = export_dataset_version_jsonl(dataset_model, version_model)
        media_type = "application/x-ndjson"
        extension = "jsonl"
    elif format is DatasetImportFormat.CSV:
        content = export_dataset_version_csv(dataset_model, version_model)
        media_type = "text/csv"
        extension = "csv"
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="unsupported export format",
        )
    filename = f"dataset-{dataset.id}-v{version.version}.{extension}"
    return Response(
        content=content.encode("utf-8"),
        media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/{dataset_id}/versions", response_model=DatasetVersion, status_code=status.HTTP_201_CREATED
)
def create_dataset_version(
    project_id: str,
    dataset_id: str,
    payload: DatasetVersionCreateRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> DatasetVersion:
    dataset = get_dataset(db, project_id, dataset_id)
    metadata = (
        payload.metadata
        if payload.metadata is not None
        else (dataset.current_version.metadata_json if dataset.current_version else {})
    )
    return version_response(create_version(db, dataset, payload.cases, metadata))


@router.get("/{dataset_id}/versions/{version_id}/cases", response_model=list[DatasetCase])
def list_cases(
    project_id: str,
    dataset_id: str,
    version_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> list[DatasetCase]:
    version = get_version(db, project_id, dataset_id, version_id)
    return [case_response(case) for case in sorted(version.cases, key=lambda item: item.case_key)]


@router.post("/{dataset_id}/imports/preview", response_model=DatasetImportPreviewResponse)
def preview_import(
    project_id: str,
    dataset_id: str,
    payload: DatasetImportRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> DatasetImportPreviewResponse:
    get_dataset(db, project_id, dataset_id)
    return import_preview_response(parse_import(payload))


@router.post(
    "/{dataset_id}/imports/commit",
    response_model=DatasetImportCommitResponse,
    status_code=status.HTTP_201_CREATED,
)
def commit_import(
    project_id: str,
    dataset_id: str,
    payload: DatasetImportCommitRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> DatasetImportCommitResponse:
    dataset = get_dataset(db, project_id, dataset_id)
    parsed = parse_import(payload)
    preview = import_preview_response(parsed)
    if not parsed.cases or (parsed.issues and not payload.allow_partial):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=preview.model_dump(mode="json"),
        )
    metadata = (
        payload.metadata
        if payload.metadata is not None
        else (dataset.current_version.metadata_json if dataset.current_version else {})
    )
    return DatasetImportCommitResponse(
        dataset_version=version_response(create_version(db, dataset, parsed.cases, metadata)),
        issues=preview.issues,
    )


@router.post("/{dataset_id}/imports/cancel", status_code=status.HTTP_204_NO_CONTENT)
def cancel_import(
    project_id: str,
    dataset_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> None:
    get_dataset(db, project_id, dataset_id)


@router.post(
    "/{dataset_id}/versions/{version_id}/cases",
    response_model=DatasetVersion,
    status_code=status.HTTP_201_CREATED,
)
def create_case(
    project_id: str,
    dataset_id: str,
    version_id: str,
    payload: DatasetCaseCreateRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> DatasetVersion:
    version = get_version(db, project_id, dataset_id, version_id)
    cases = [case_response(case) for case in version.cases]
    if any(case.id == payload.id for case in cases):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="dataset case already exists"
        )
    cases.append(payload)
    dataset = get_dataset(db, project_id, dataset_id)
    return version_response(create_version(db, dataset, cases, version.metadata_json))


@router.patch(
    "/{dataset_id}/versions/{version_id}/cases/{case_id}",
    response_model=DatasetVersion,
)
def update_case(
    project_id: str,
    dataset_id: str,
    version_id: str,
    case_id: str,
    payload: DatasetCaseCreateRequest,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> DatasetVersion:
    version = get_version(db, project_id, dataset_id, version_id)
    cases = [case_response(case) for case in version.cases]
    if payload.id != case_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="case id is immutable"
        )
    for index, case in enumerate(cases):
        if case.id == case_id:
            cases[index] = payload
            break
    else:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dataset case not found")
    dataset = get_dataset(db, project_id, dataset_id)
    return version_response(create_version(db, dataset, cases, version.metadata_json))


@router.delete(
    "/{dataset_id}/versions/{version_id}/cases/{case_id}",
    response_model=DatasetVersion,
    status_code=status.HTTP_201_CREATED,
)
def archive_case(
    project_id: str,
    dataset_id: str,
    version_id: str,
    case_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> DatasetVersion:
    """Archive a Case by creating a new Dataset Version without mutating history."""
    version = get_version(db, project_id, dataset_id, version_id)
    cases = [case_response(case) for case in version.cases]
    remaining = [case for case in cases if case.id != case_id]
    if len(remaining) == len(cases):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dataset case not found")
    dataset = get_dataset(db, project_id, dataset_id)
    return version_response(create_version(db, dataset, remaining, version.metadata_json))


def select_trace_value(trace: Trace, selection: TraceFieldSelection) -> Any:
    span = next((item for item in trace.spans if item.span_id == selection.span_id), None)
    if span is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="span not found",
        )
    value = getattr(span, selection.field)
    if selection.attribute_key is not None:
        if not isinstance(value, dict) or selection.attribute_key not in value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="selected trace attribute not found",
            )
        value = value[selection.attribute_key]
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="selected trace value is empty"
        )
    return value


def default_trace_value(trace: Trace, field: str) -> Any:
    value = optional_default_trace_value(trace, field)
    if value is not None:
        return value
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"trace has no usable {field}",
    )


def optional_default_trace_value(trace: Trace, field: str) -> Any | None:
    for span in trace.spans:
        if span.kind.value == "agent" and getattr(span, field) is not None:
            return getattr(span, field)
    for span in trace.spans:
        if getattr(span, field) is not None:
            return getattr(span, field)
    return None


def optional_default_trace_span_id(trace: Trace, field: str) -> str | None:
    for span in trace.spans:
        if span.kind.value == "agent" and getattr(span, field) is not None:
            return span.span_id
    for span in trace.spans:
        if getattr(span, field) is not None:
            return span.span_id
    return None


def default_trace_span_id(trace: Trace, field: str) -> str:
    span_id = optional_default_trace_span_id(trace, field)
    if span_id is not None:
        return span_id
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"trace has no usable {field}",
    )


def trace_tool_calls(trace: Trace, tool_span_ids: list[str] | None) -> list[ExpectedToolCall]:
    if tool_span_ids is not None and len(tool_span_ids) != len(set(tool_span_ids)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="tool span ids must be unique",
        )
    requested = set(tool_span_ids) if tool_span_ids is not None else None
    expected_tools: list[ExpectedToolCall] = []
    for span in trace.spans:
        if span.kind.value != "tool" or (requested is not None and span.span_id not in requested):
            continue
        arguments = (
            span.attributes.get("tool.call.arguments")
            or span.attributes.get("tool.parameters")
            or span.input
            or {}
        )
        expected_tools.append(
            ExpectedToolCall(
                name=str(span.attributes.get("tool.name") or span.name),
                arguments=arguments if isinstance(arguments, dict) else {"input": arguments},
                order=len(expected_tools),
            )
        )
    if requested is not None and len(expected_tools) != len(requested):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="one or more selected tool spans were not found",
        )
    return expected_tools


@router.post(
    "/{dataset_id}/versions/{version_id}/cases/from-trace",
    response_model=DatasetVersion,
    status_code=status.HTTP_201_CREATED,
)
def create_case_from_trace(
    project_id: str,
    dataset_id: str,
    version_id: str,
    payload: TraceToDatasetCaseRequest,
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> DatasetVersion:
    version = get_version(db, project_id, dataset_id, version_id)
    if any(case.case_key == payload.id for case in version.cases):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="dataset case already exists"
        )
    trace = trace_response(get_project_trace(db, project_id, payload.trace_id), settings)
    input_value = (
        select_trace_value(trace, payload.input)
        if payload.input is not None
        else default_trace_value(trace, "input")
    )
    expected_output = (
        select_trace_value(trace, payload.expected_output)
        if payload.expected_output is not None
        else optional_default_trace_value(trace, "output")
    )
    expected_state = (
        select_trace_value(trace, payload.expected_state)
        if payload.expected_state is not None
        else None
    )
    if expected_state is not None and not isinstance(expected_state, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="selected expected state must be an object",
        )
    mapped_metadata = dict(payload.metadata)
    for key, selection in payload.metadata_mapping.items():
        mapped_metadata[key] = select_trace_value(trace, selection)
    cases = [case_response(case) for case in version.cases]
    source_span_ids = list(
        dict.fromkeys(
            span_id
            for span_id in [
                payload.input.span_id
                if payload.input is not None
                else default_trace_span_id(trace, "input"),
                payload.expected_output.span_id
                if payload.expected_output is not None
                else optional_default_trace_span_id(trace, "output"),
                payload.expected_state.span_id if payload.expected_state else None,
            ]
            + (
                payload.tool_span_ids
                if payload.tool_span_ids is not None
                else [span.span_id for span in trace.spans if span.kind.value == "tool"]
            )
            + [selection.span_id for selection in payload.metadata_mapping.values()]
            if span_id is not None
        )
    )
    source_mapping = {
        "input": payload.input.model_dump(mode="json") if payload.input else None,
        "expected_output": (
            payload.expected_output.model_dump(mode="json")
            if payload.expected_output
            else None
        ),
        "expected_state": (
            payload.expected_state.model_dump(mode="json") if payload.expected_state else None
        ),
        "tool_span_ids": payload.tool_span_ids,
        "metadata_mapping": {
            key: selection.model_dump(mode="json")
            for key, selection in payload.metadata_mapping.items()
        },
    }
    cases.append(
        DatasetCase(
            id=payload.id,
            input=input_value,
            expected_output=expected_output,
            expected_tools=trace_tool_calls(trace, payload.tool_span_ids),
            expected_state=expected_state,
            metadata={**mapped_metadata, "source": "trace"},
            source_trace_id=trace.trace_id,
            source_span_ids=source_span_ids,
            source_mapping=source_mapping,
        )
    )
    dataset = get_dataset(db, project_id, dataset_id)
    return version_response(create_version(db, dataset, cases, version.metadata_json))
