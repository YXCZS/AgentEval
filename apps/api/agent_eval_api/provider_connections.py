"""Secret-safe reads for platform-managed model provider connections."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent_eval_api.auth import AuthContext, get_db, require_project_access
from agent_eval_api.contracts import (
    ProviderConnection,
    ProviderConnectionCreateRequest,
    ProviderConnectionExport,
    ProviderConnectionRotateRequest,
    ProviderConnectionTestRequest,
    ProviderConnectionTestResponse,
)
from agent_eval_api.credential_encryption import (
    CredentialCipher,
    CredentialEncryptionError,
)
from agent_eval_api.db import (
    EvaluatorVersionRecord,
    ProjectRecord,
    ProviderConnectionRecord,
    new_id,
    utc_now,
)
from agent_eval_api.provider_validation import (
    ProviderValidationError,
    ProviderValidationEvidence,
    validate_provider_connection,
)
from agent_eval_api.settings import Settings, get_settings
from agent_eval_api.trace_privacy import PrivacyStats, is_sensitive_key, sanitize_value

router = APIRouter(
    prefix="/projects/{project_id}/provider-connections",
    tags=["provider-connections"],
)


def _require_project(db: Session, project_id: str) -> None:
    if db.get(ProjectRecord, project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")


def _public_connection(
    record: ProviderConnectionRecord, settings: Settings
) -> ProviderConnection:
    parameters = sanitize_value(
        _remove_sensitive_parameter_keys(record.default_parameters, settings),
        settings,
        PrivacyStats(),
    )
    if not isinstance(parameters, dict):
        raise ValueError("provider default parameters must be an object")
    return ProviderConnection.model_validate(
        {
            "id": record.id,
            "project_id": record.project_id,
            "name": record.name,
            "provider": record.provider,
            "base_url": record.base_url,
            "model": record.model,
            "default_parameters": parameters,
            "credential_mask": record.credential_mask,
            "credential_key_id": record.credential_key_id,
            "status": record.status,
            "enabled": record.enabled,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "tested_at": record.tested_at,
        }
    )


def _remove_sensitive_parameter_keys(value: object, settings: Settings) -> object:
    if isinstance(value, dict):
        return {
            key: _remove_sensitive_parameter_keys(item, settings)
            for key, item in value.items()
            if not is_sensitive_key(str(key), settings)
        }
    if isinstance(value, list):
        return [_remove_sensitive_parameter_keys(item, settings) for item in value]
    return value


def _contains_sensitive_parameter_key(value: object, settings: Settings) -> bool:
    if isinstance(value, dict):
        return any(
            is_sensitive_key(str(key), settings)
            or _contains_sensitive_parameter_key(item, settings)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_parameter_key(item, settings) for item in value)
    return False


def _project_connections(db: Session, project_id: str) -> list[ProviderConnectionRecord]:
    return list(
        db.scalars(
            select(ProviderConnectionRecord)
            .where(ProviderConnectionRecord.project_id == project_id)
            .order_by(ProviderConnectionRecord.created_at.desc())
        ).all()
    )


def _connection_or_404(
    db: Session,
    project_id: str,
    connection_id: str,
) -> ProviderConnectionRecord:
    record = db.scalar(
        select(ProviderConnectionRecord).where(
            ProviderConnectionRecord.id == connection_id,
            ProviderConnectionRecord.project_id == project_id,
        )
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="provider connection not found",
        )
    return record


def _validate_provider(
    payload: ProviderConnectionTestRequest,
) -> ProviderValidationEvidence:
    try:
        return validate_provider_connection(
            base_url=str(payload.base_url),
            api_key=payload.api_key.get_secret_value(),
            model=payload.model,
            timeout_seconds=payload.timeout_seconds,
        )
    except ProviderValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.post("/test", response_model=ProviderConnectionTestResponse)
def test_provider_connection(
    project_id: str,
    payload: ProviderConnectionTestRequest,
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> ProviderConnectionTestResponse:
    _require_project(db, project_id)
    if _contains_sensitive_parameter_key(payload.default_parameters, settings):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="default parameters must not contain credentials",
        )
    evidence = _validate_provider(payload)
    return ProviderConnectionTestResponse(
        provider=payload.provider,
        configured_model=payload.model,
        response_model=evidence.response_model,
        upstream_request_id=evidence.upstream_request_id,
        input_tokens=evidence.input_tokens,
        output_tokens=evidence.output_tokens,
        total_tokens=evidence.total_tokens,
    )


@router.post("", response_model=ProviderConnection, status_code=status.HTTP_201_CREATED)
def create_provider_connection(
    project_id: str,
    payload: ProviderConnectionCreateRequest,
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> ProviderConnection:
    _require_project(db, project_id)
    if _contains_sensitive_parameter_key(payload.default_parameters, settings):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="default parameters must not contain credentials",
        )
    duplicate = db.scalar(
        select(ProviderConnectionRecord.id).where(
            ProviderConnectionRecord.project_id == project_id,
            ProviderConnectionRecord.name == payload.name,
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="provider connection name already exists",
        )

    credential = payload.api_key.get_secret_value()
    connection_id = new_id()
    try:
        encrypted = CredentialCipher.from_settings(settings).encrypt(
            credential,
            project_id=project_id,
            connection_id=connection_id,
        )
    except CredentialEncryptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    _validate_provider(payload)

    now = utc_now()
    record = ProviderConnectionRecord(
        id=connection_id,
        project_id=project_id,
        name=payload.name,
        provider=payload.provider.value,
        base_url=str(payload.base_url).rstrip("/"),
        model=payload.model,
        default_parameters=payload.default_parameters,
        credential_mask=encrypted.mask,
        credential_key_id=encrypted.key_id,
        credential_ciphertext=encrypted.ciphertext,
        credential_nonce=encrypted.nonce,
        status="active",
        enabled=True,
        tested_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="provider connection name already exists",
        ) from exc
    db.refresh(record)
    return _public_connection(record, settings)


@router.get("", response_model=list[ProviderConnection])
def list_provider_connections(
    project_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> list[ProviderConnection]:
    _require_project(db, project_id)
    return [
        _public_connection(record, settings)
        for record in _project_connections(db, project_id)
    ]


@router.get("/export", response_model=ProviderConnectionExport)
def export_provider_connections(
    project_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> ProviderConnectionExport:
    _require_project(db, project_id)
    return ProviderConnectionExport(
        generated_at=utc_now(),
        connections=[
            _public_connection(record, settings)
            for record in _project_connections(db, project_id)
        ],
    )


@router.get("/{connection_id}", response_model=ProviderConnection)
def read_provider_connection(
    project_id: str,
    connection_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> ProviderConnection:
    record = _connection_or_404(db, project_id, connection_id)
    return _public_connection(record, settings)


@router.patch("/{connection_id}/enabled", response_model=ProviderConnection)
def set_provider_connection_enabled(
    project_id: str,
    connection_id: str,
    enabled: bool,
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> ProviderConnection:
    record = _connection_or_404(db, project_id, connection_id)
    record.enabled = enabled
    record.status = "active" if enabled else "disabled"
    record.updated_at = utc_now()
    db.commit()
    db.refresh(record)
    return _public_connection(record, settings)


@router.post("/{connection_id}/rotate", response_model=ProviderConnection)
def rotate_provider_connection_credential(
    project_id: str,
    connection_id: str,
    payload: ProviderConnectionRotateRequest,
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> ProviderConnection:
    record = _connection_or_404(db, project_id, connection_id)
    credential = payload.api_key.get_secret_value()
    try:
        validate_provider_connection(
            base_url=record.base_url,
            api_key=credential,
            model=record.model,
            timeout_seconds=payload.timeout_seconds,
        )
    except ProviderValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    try:
        encrypted = CredentialCipher.from_settings(settings).encrypt(
            credential,
            project_id=project_id,
            connection_id=connection_id,
        )
    except CredentialEncryptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    now = utc_now()
    record.credential_mask = encrypted.mask
    record.credential_key_id = encrypted.key_id
    record.credential_ciphertext = encrypted.ciphertext
    record.credential_nonce = encrypted.nonce
    record.status = "active"
    record.enabled = True
    record.tested_at = now
    record.updated_at = now
    db.commit()
    db.refresh(record)
    return _public_connection(record, settings)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider_connection(
    project_id: str,
    connection_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> None:
    record = _connection_or_404(db, project_id, connection_id)
    bound_evaluator = db.scalar(
        select(EvaluatorVersionRecord.id).where(
            EvaluatorVersionRecord.project_id == project_id,
            EvaluatorVersionRecord.provider_connection_id == connection_id,
        )
    )
    if bound_evaluator is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="provider connection is referenced by an immutable Judge evaluator",
        )
    db.delete(record)
    db.commit()
