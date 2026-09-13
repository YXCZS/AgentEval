"""Signed, language-neutral protocol for one remote Experiment trigger."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import MutableSet
from dataclasses import dataclass
from typing import Any

_VERSION = "v1"
_TIMESTAMP_HEADER = "X-Agent-Eval-Trigger-Timestamp"
_DELIVERY_ID_HEADER = "X-Agent-Eval-Trigger-Delivery-Id"


class RemoteTriggerSignatureError(ValueError):
    """A remote runner received an unauthenticated trigger delivery."""


@dataclass(frozen=True)
class RemoteTriggerDelivery:
    """Exact payload and headers to send without persisting a plaintext secret."""

    delivery_id: str
    timestamp: int
    body: bytes
    headers: dict[str, str]


def _signature_input(timestamp: int, delivery_id: str, body: bytes) -> bytes:
    return f"{timestamp}.{delivery_id}.".encode("ascii") + body


def _signature(signing_secret: str, timestamp: int, delivery_id: str, body: bytes) -> str:
    return hmac.new(
        signing_secret.encode("utf-8"),
        _signature_input(timestamp, delivery_id, body),
        hashlib.sha256,
    ).hexdigest()


def build_remote_trigger_delivery(
    payload: dict[str, Any],
    *,
    signing_secret: str,
    signature_header: str,
    delivery_id: str,
    timestamp: int | None = None,
) -> RemoteTriggerDelivery:
    """Serialize and sign the exact JSON bytes delivered to a remote runtime."""

    if not signing_secret:
        raise RemoteTriggerSignatureError("remote trigger signing secret is unavailable")
    if not delivery_id:
        raise RemoteTriggerSignatureError("remote trigger delivery id is required")
    if not signature_header:
        raise RemoteTriggerSignatureError("remote trigger signature header is required")

    delivery_timestamp = int(time.time()) if timestamp is None else timestamp
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return RemoteTriggerDelivery(
        delivery_id=delivery_id,
        timestamp=delivery_timestamp,
        body=body,
        headers={
            "Content-Type": "application/json",
            _TIMESTAMP_HEADER: str(delivery_timestamp),
            _DELIVERY_ID_HEADER: delivery_id,
            signature_header: (
                f"{_VERSION}="
                f"{_signature(signing_secret, delivery_timestamp, delivery_id, body)}"
            ),
        },
    )


def verify_remote_trigger_delivery(
    headers: dict[str, str],
    body: bytes,
    *,
    signing_secret: str,
    signature_header: str = "X-Agent-Eval-Trigger-Signature",
    max_age_seconds: int = 300,
    replay_cache: MutableSet[str] | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Validate a delivery before a remote runtime starts an Experiment.

    ``replay_cache`` belongs to the receiving runtime and must retain IDs for at
    least ``max_age_seconds``. The ID is recorded only after all authentication
    checks succeed, so an invalid request cannot poison the cache.
    """

    if not signing_secret:
        raise RemoteTriggerSignatureError("remote trigger signing secret is unavailable")
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")

    timestamp_text = headers.get(_TIMESTAMP_HEADER)
    delivery_id = headers.get(_DELIVERY_ID_HEADER)
    received_signature = headers.get(signature_header)
    if timestamp_text is None or not delivery_id or not received_signature:
        raise RemoteTriggerSignatureError("remote trigger authentication headers are missing")
    try:
        timestamp = int(timestamp_text)
    except ValueError as exc:
        raise RemoteTriggerSignatureError("remote trigger timestamp is invalid") from exc

    current_time = int(time.time()) if now is None else now
    if abs(current_time - timestamp) > max_age_seconds:
        raise RemoteTriggerSignatureError("remote trigger delivery has expired")
    if replay_cache is not None and delivery_id in replay_cache:
        raise RemoteTriggerSignatureError("remote trigger delivery was already processed")

    expected_signature = f"{_VERSION}={_signature(signing_secret, timestamp, delivery_id, body)}"
    if not hmac.compare_digest(received_signature, expected_signature):
        raise RemoteTriggerSignatureError("remote trigger signature is invalid")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RemoteTriggerSignatureError("remote trigger payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RemoteTriggerSignatureError("remote trigger payload must be a JSON object")

    if replay_cache is not None:
        replay_cache.add(delivery_id)
    return payload
