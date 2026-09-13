"""HTTP clients for the user-managed Agent and LLM Judge protocols.

These adapters deliberately accept credentials at call time only. Connection
records store references, while the worker or deployment supplies the actual
runtime secret through its credential resolver.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from agent_eval_api.contracts import (
    ExternalJudgeRequest,
    ExternalJudgeResponse,
)


class ExternalProtocolError(RuntimeError):
    """An external endpoint did not satisfy transport or JSON protocol rules."""

    def __init__(self, error_type: str, message: str, *, attempts: int = 1) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.attempts = attempts


@dataclass(frozen=True)
class ExternalJudgeConfig:
    connection_id: str
    endpoint: str
    timeout_seconds: float = 60.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.2


@dataclass(frozen=True)
class ExternalJudgeResult:
    response: ExternalJudgeResponse
    raw_response: dict[str, Any]
    attempts: int


def _request_body(request: ExternalJudgeRequest) -> bytes:
    return json.dumps(
        request.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _headers(signing_secret: str, body: bytes, timestamp: int) -> dict[str, str]:
    if not signing_secret:
        raise ExternalProtocolError(
            "authentication_error", "external Judge signing secret is unavailable"
        )
    timestamp_text = str(timestamp)
    signature_input = timestamp_text.encode("ascii") + b"." + body
    digest = hmac.new(
        signing_secret.encode("utf-8"), signature_input, hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Agent-Eval-Timestamp": timestamp_text,
        "X-Agent-Eval-Signature": f"v1={digest}",
    }


async def call_external_judge(
    config: ExternalJudgeConfig,
    request: ExternalJudgeRequest,
    *,
    signing_secret: str,
    client: httpx.AsyncClient | None = None,
) -> ExternalJudgeResult:
    """Call a signed user-managed Judge and validate its normalized response."""

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=config.timeout_seconds)
    attempt = 0
    request_body = _request_body(request)
    try:
        while True:
            try:
                response = await http_client.post(
                    config.endpoint,
                    headers=_headers(signing_secret, request_body, int(time.time())),
                    content=request_body,
                )
                if response.status_code in {401, 403}:
                    raise ExternalProtocolError(
                        "authentication_error",
                        f"external Judge returned HTTP {response.status_code}",
                        attempts=attempt + 1,
                    )
                if response.status_code == 429:
                    raise ExternalProtocolError(
                        "rate_limit", "external Judge returned HTTP 429", attempts=attempt + 1
                    )
                if response.status_code >= 500:
                    raise ExternalProtocolError(
                        "service_error",
                        f"external Judge returned HTTP {response.status_code}",
                        attempts=attempt + 1,
                    )
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    raise ExternalProtocolError(
                        "protocol_error", "external Judge response must be a JSON object"
                    )
                try:
                    normalized = ExternalJudgeResponse.model_validate(body)
                except ValueError as exc:
                    raise ExternalProtocolError(
                        "protocol_error", "external Judge response has an invalid schema"
                    ) from exc
                provenance = normalized.provenance
                if provenance.source != "external_judge":
                    raise ExternalProtocolError(
                        "protocol_error",
                        "external Judge response has an invalid provenance source",
                        attempts=attempt + 1,
                    )
                if provenance.evaluator_version != request.evaluator_version:
                    raise ExternalProtocolError(
                        "protocol_error",
                        "external Judge response evaluator version does not match the request",
                        attempts=attempt + 1,
                    )
                if provenance.protocol not in {None, "signed_http_json_v1"}:
                    raise ExternalProtocolError(
                        "protocol_error",
                        "external Judge response has an invalid provenance protocol",
                        attempts=attempt + 1,
                    )
                if provenance.connection_id not in {None, config.connection_id}:
                    raise ExternalProtocolError(
                        "protocol_error",
                        "external Judge response has an invalid provenance connection",
                        attempts=attempt + 1,
                    )
                normalized = normalized.model_copy(
                    update={
                        "provenance": provenance.model_copy(
                            update={
                                "source": "external_judge",
                                "protocol": "signed_http_json_v1",
                                "connection_id": config.connection_id,
                            }
                        )
                    }
                )
                return ExternalJudgeResult(
                    response=normalized,
                    raw_response=body,
                    attempts=attempt + 1,
                )
            except ExternalProtocolError as exc:
                if exc.error_type not in {"rate_limit", "service_error"}:
                    raise
                if attempt >= config.max_retries:
                    raise ExternalProtocolError(
                        exc.error_type, str(exc), attempts=attempt + 1
                    ) from exc
            except httpx.TimeoutException as exc:
                if attempt >= config.max_retries:
                    raise ExternalProtocolError(
                        "timeout", "external Judge request timed out", attempts=attempt + 1
                    ) from exc
            except httpx.HTTPStatusError as exc:
                raise ExternalProtocolError(
                    "provider_error",
                    f"external Judge request failed: HTTP {exc.response.status_code}",
                    attempts=attempt + 1,
                ) from exc
            except httpx.HTTPError as exc:
                if attempt >= config.max_retries:
                    raise ExternalProtocolError(
                        "connection_error",
                        "external Judge request could not be completed",
                        attempts=attempt + 1,
                    ) from exc
            except ValueError as exc:
                raise ExternalProtocolError(
                    "protocol_error",
                    "external Judge response is not valid JSON",
                    attempts=attempt + 1,
                ) from exc
            await asyncio.sleep(config.retry_backoff_seconds * (2**attempt))
            attempt += 1
    finally:
        if owns_client:
            await http_client.aclose()
