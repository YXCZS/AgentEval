"""Dataset-scoped configuration for externally hosted Agent runners."""

from __future__ import annotations

import secrets
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_eval_api.auth import AuthContext, get_db, require_project_access
from agent_eval_api.contracts import (
    RemoteTrigger,
    RemoteTriggerCreated,
    RemoteTriggerCreateRequest,
    RemoteTriggerDeliveryStatus,
    RemoteTriggerUpdateRequest,
)
from agent_eval_api.contracts import (
    RemoteTriggerDelivery as RemoteTriggerDeliveryResponse,
)
from agent_eval_api.credential_encryption import (
    CredentialCipher,
    CredentialEncryptionError,
    EncryptedCredential,
)
from agent_eval_api.db import (
    DatasetRecord,
    RemoteTriggerDeliveryRecord,
    RemoteTriggerRecord,
    new_id,
    utc_now,
)
from agent_eval_api.remote_trigger_protocol import (
    RemoteTriggerDelivery,
    RemoteTriggerSignatureError,
    build_remote_trigger_delivery,
)
from agent_eval_api.settings import Settings, get_settings

router = APIRouter(
    prefix="/projects/{project_id}/datasets/{dataset_id}/remote-trigger",
    tags=["remote-triggers"],
)

_SIGNATURE_HEADER = "X-Agent-Eval-Trigger-Signature"
_TRIGGER_PURPOSE = "remote_trigger"


def _require_browser(auth: AuthContext) -> None:
    if auth.principal_type != "browser":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="browser session required to manage remote triggers",
        )


def _get_dataset(db: Session, project_id: str, dataset_id: str) -> DatasetRecord:
    dataset = db.scalar(
        select(DatasetRecord).where(
            DatasetRecord.id == dataset_id,
            DatasetRecord.project_id == project_id,
        )
    )
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dataset not found")
    return dataset


def _get_trigger(
    db: Session, project_id: str, dataset_id: str
) -> RemoteTriggerRecord:
    trigger = db.scalar(
        select(RemoteTriggerRecord).where(
            RemoteTriggerRecord.project_id == project_id,
            RemoteTriggerRecord.dataset_id == dataset_id,
        )
    )
    if trigger is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="remote trigger not found",
        )
    return trigger


def public_trigger(trigger: RemoteTriggerRecord) -> RemoteTrigger:
    return RemoteTrigger(
        id=trigger.id,
        project_id=trigger.project_id,
        dataset_id=trigger.dataset_id,
        trigger_url=HttpUrl(trigger.trigger_url),
        enabled=trigger.enabled,
        signature_header=trigger.signature_header,
        secret_mask=trigger.secret_mask,
        secret_key_id=trigger.secret_key_id,
        created_at=trigger.created_at,
        updated_at=trigger.updated_at,
    )


def _public_delivery(
    delivery: RemoteTriggerDeliveryRecord,
) -> RemoteTriggerDeliveryResponse:
    return RemoteTriggerDeliveryResponse(
        id=delivery.id,
        trigger_id=delivery.trigger_id,
        experiment_id=delivery.experiment_id,
        delivery_id=delivery.delivery_id,
        status=RemoteTriggerDeliveryStatus(delivery.status),
        attempt_count=delivery.attempt_count,
        last_http_status=delivery.last_http_status,
        last_error_type=delivery.last_error_type,
        last_error_message=delivery.last_error_message,
        accepted_at=delivery.accepted_at,
        created_at=delivery.created_at,
        updated_at=delivery.updated_at,
    )


def _new_secret(
    settings: Settings, project_id: str, trigger_id: str
) -> tuple[str, EncryptedCredential]:
    raw_secret = f"aet_{secrets.token_urlsafe(32)}"
    try:
        encrypted = CredentialCipher.from_settings(settings).encrypt(
            raw_secret,
            project_id=project_id,
            connection_id=trigger_id,
            purpose=_TRIGGER_PURPOSE,
        )
    except CredentialEncryptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    return raw_secret, encrypted


def create_remote_trigger_record(
    *,
    db: Session,
    settings: Settings,
    project_id: str,
    dataset_id: str,
    trigger_url: str,
    enabled: bool = True,
) -> tuple[RemoteTriggerRecord, str]:
    """Stage a Dataset trigger and return its one-time secret.

    Callers own the transaction so legacy Release migration can remain atomic.
    """

    payload = RemoteTriggerCreateRequest(
        trigger_url=HttpUrl(trigger_url), enabled=enabled
    )
    existing = db.scalar(
        select(RemoteTriggerRecord.id).where(
            RemoteTriggerRecord.project_id == project_id,
            RemoteTriggerRecord.dataset_id == dataset_id,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="remote trigger already exists; rotate its secret instead",
        )

    trigger_id = new_id()
    raw_secret, encrypted = _new_secret(settings, project_id, trigger_id)
    now = utc_now()
    trigger = RemoteTriggerRecord(
        id=trigger_id,
        project_id=project_id,
        dataset_id=dataset_id,
        trigger_url=str(payload.trigger_url),
        enabled=payload.enabled,
        signature_header=_SIGNATURE_HEADER,
        secret_mask=encrypted.mask,
        secret_key_id=encrypted.key_id,
        secret_ciphertext=encrypted.ciphertext,
        secret_nonce=encrypted.nonce,
        created_at=now,
        updated_at=now,
    )
    db.add(trigger)
    return trigger, raw_secret


@router.get("", response_model=RemoteTrigger)
def read_remote_trigger(
    project_id: str,
    dataset_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> RemoteTrigger:
    _get_dataset(db, project_id, dataset_id)
    return public_trigger(_get_trigger(db, project_id, dataset_id))


@router.post("", response_model=RemoteTriggerCreated, status_code=status.HTTP_201_CREATED)
def create_remote_trigger(
    project_id: str,
    dataset_id: str,
    payload: RemoteTriggerCreateRequest,
    db: Session = Depends(get_db),  # noqa: B008
    auth: AuthContext = Depends(require_project_access),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> RemoteTriggerCreated:
    _require_browser(auth)
    _get_dataset(db, project_id, dataset_id)
    trigger, raw_secret = create_remote_trigger_record(
        db=db,
        settings=settings,
        project_id=project_id,
        dataset_id=dataset_id,
        trigger_url=str(payload.trigger_url),
        enabled=payload.enabled,
    )
    db.commit()
    db.refresh(trigger)
    return RemoteTriggerCreated(**public_trigger(trigger).model_dump(), signing_secret=raw_secret)


@router.patch("", response_model=RemoteTrigger)
def update_remote_trigger(
    project_id: str,
    dataset_id: str,
    payload: RemoteTriggerUpdateRequest,
    db: Session = Depends(get_db),  # noqa: B008
    auth: AuthContext = Depends(require_project_access),  # noqa: B008
) -> RemoteTrigger:
    _require_browser(auth)
    trigger = _get_trigger(db, project_id, dataset_id)
    if "trigger_url" in payload.model_fields_set and payload.trigger_url is not None:
        trigger.trigger_url = str(payload.trigger_url)
    if "enabled" in payload.model_fields_set and payload.enabled is not None:
        trigger.enabled = payload.enabled
    trigger.updated_at = utc_now()
    db.commit()
    db.refresh(trigger)
    return public_trigger(trigger)


@router.get("/deliveries", response_model=list[RemoteTriggerDeliveryResponse])
def list_remote_trigger_deliveries(
    project_id: str,
    dataset_id: str,
    db: Session = Depends(get_db),  # noqa: B008
    _: AuthContext = Depends(require_project_access),  # noqa: B008
) -> list[RemoteTriggerDeliveryResponse]:
    """Show safe delivery state; receipt is intentionally not Experiment completion."""

    _get_dataset(db, project_id, dataset_id)
    trigger = _get_trigger(db, project_id, dataset_id)
    records = db.scalars(
        select(RemoteTriggerDeliveryRecord)
        .where(RemoteTriggerDeliveryRecord.trigger_id == trigger.id)
        .order_by(RemoteTriggerDeliveryRecord.created_at.desc())
    ).all()
    return [_public_delivery(record) for record in records]


def decrypt_trigger_secret(
    trigger: RemoteTriggerRecord, settings: Settings
) -> str:
    """Decrypt only at delivery time; the caller must never persist the result."""

    try:
        return CredentialCipher.from_settings(settings).decrypt(
            ciphertext=trigger.secret_ciphertext,
            nonce=trigger.secret_nonce,
            stored_key_id=trigger.secret_key_id,
            project_id=trigger.project_id,
            connection_id=trigger.id,
            purpose=_TRIGGER_PURPOSE,
        )
    except CredentialEncryptionError:
        raise


class RemoteTriggerDeliveryError(RuntimeError):
    """The remote runner did not accept a signed Experiment trigger."""

    def __init__(
        self,
        error_type: str,
        *,
        retryable: bool,
        http_status: int | None = None,
    ) -> None:
        super().__init__("remote trigger delivery was not accepted")
        self.error_type = error_type
        self.retryable = retryable
        self.http_status = http_status


def build_experiment_trigger_delivery(
    *,
    trigger: RemoteTriggerRecord,
    experiment_id: str,
    experiment_name: str,
    dataset_version_id: str,
    dataset_version: int,
    agent_release: str,
    evidence_policy: str,
    callback_base_url: str,
    settings: Settings,
    delivery_id: str | None = None,
) -> RemoteTriggerDelivery:
    """Create the non-secret, run-level trigger sent to the user-owned runtime."""

    base_url = callback_base_url.rstrip("/")
    payload: dict[str, Any] = {
        "protocol": "agent_eval_remote_trigger_v1",
        "experiment": {
            "id": experiment_id,
            "name": experiment_name,
            "execution_mode": "remote_trigger",
            "agent_release": agent_release,
            "evidence_policy": evidence_policy,
        },
        "dataset": {
            "id": trigger.dataset_id,
            "version_id": dataset_version_id,
            "version": dataset_version,
        },
        "callback": {
            "api_base_url": base_url,
            "manifest_url": (
                f"{base_url}/projects/{trigger.project_id}/experiments/{experiment_id}/manifest"
            ),
            "item_start_url": (
                f"{base_url}/projects/{trigger.project_id}/experiments/{experiment_id}/items/start"
            ),
            "item_complete_url_template": (
                f"{base_url}/projects/{trigger.project_id}/experiments/{experiment_id}"
                "/items/{item_id}/complete"
            ),
            "item_fail_url_template": (
                f"{base_url}/projects/{trigger.project_id}/experiments/{experiment_id}"
                "/items/{item_id}/fail"
            ),
            "trace_otlp_url": f"{base_url}/projects/{trigger.project_id}/traces/otlp",
            "finalize_url": (
                f"{base_url}/projects/{trigger.project_id}/experiments/{experiment_id}/finalize"
            ),
        },
    }
    try:
        signing_secret = decrypt_trigger_secret(trigger, settings)
        return build_remote_trigger_delivery(
            payload,
            signing_secret=signing_secret,
            signature_header=trigger.signature_header,
            delivery_id=delivery_id or new_id(),
        )
    except (CredentialEncryptionError, RemoteTriggerSignatureError) as exc:
        raise RemoteTriggerDeliveryError("signing_unavailable", retryable=False) from exc


def dispatch_experiment_trigger(
    trigger: RemoteTriggerRecord,
    delivery: RemoteTriggerDelivery,
) -> int:
    """Send one signed trigger and classify only safe-to-store failure metadata."""

    try:
        response = httpx.post(
            trigger.trigger_url,
            headers=delivery.headers,
            content=delivery.body,
            timeout=10.0,
        )
    except httpx.TimeoutException as exc:
        raise RemoteTriggerDeliveryError("timeout", retryable=True) from exc
    except httpx.TransportError as exc:
        raise RemoteTriggerDeliveryError("network", retryable=True) from exc

    if 200 <= response.status_code < 300:
        return response.status_code
    if response.status_code in {408, 425, 429} or response.status_code >= 500:
        raise RemoteTriggerDeliveryError(
            f"http_{response.status_code}",
            retryable=True,
            http_status=response.status_code,
        )
    raise RemoteTriggerDeliveryError(
        f"http_{response.status_code}",
        retryable=False,
        http_status=response.status_code,
    )


def deliver_experiment_trigger(
    *,
    db: Session,
    trigger: RemoteTriggerRecord,
    record: RemoteTriggerDeliveryRecord,
    delivery: RemoteTriggerDelivery,
    max_attempts: int = 3,
) -> RemoteTriggerDeliveryRecord:
    """Deliver a run trigger with bounded retries and no upstream response storage."""

    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")

    for attempt in range(1, max_attempts + 1):
        record.status = "delivering"
        record.attempt_count = attempt
        record.last_error_type = None
        record.last_error_message = None
        record.updated_at = utc_now()
        db.commit()
        try:
            http_status = dispatch_experiment_trigger(trigger, delivery)
        except RemoteTriggerDeliveryError as exc:
            record.last_http_status = exc.http_status
            record.last_error_type = exc.error_type
            record.last_error_message = "remote runner did not accept this delivery"
            record.updated_at = utc_now()
            if not exc.retryable:
                record.status = "rejected"
                db.commit()
                db.refresh(record)
                return record
            if attempt == max_attempts:
                record.status = "failed"
                db.commit()
                db.refresh(record)
                return record
            db.commit()
            time.sleep(0.2 * (2 ** (attempt - 1)))
            continue

        record.status = "accepted"
        record.last_http_status = http_status
        record.last_error_type = None
        record.last_error_message = None
        record.accepted_at = utc_now()
        record.updated_at = record.accepted_at
        db.commit()
        db.refresh(record)
        return record

    raise AssertionError("remote trigger delivery loop exited unexpectedly")
