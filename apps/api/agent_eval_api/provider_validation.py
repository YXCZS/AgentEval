"""Real connectivity validation for OpenAI-compatible provider connections."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    NotFoundError,
    OpenAI,
)


class ProviderValidationError(RuntimeError):
    """A credential-safe provider connectivity or protocol failure."""


@dataclass(frozen=True)
class ProviderValidationEvidence:
    response_model: str
    upstream_request_id: str
    input_tokens: int
    output_tokens: int
    total_tokens: int


def _usage_value(usage: Any, field: str) -> int:
    value = getattr(usage, field, None)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProviderValidationError(f"provider response has invalid usage.{field}")
    return value


def validate_provider_connection(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: float,
) -> ProviderValidationEvidence:
    """Make one real request and require independently verifiable response evidence."""

    challenge = f"AGENT_EVAL_{secrets.token_hex(12)}"
    client: OpenAI | None = None
    try:
        client = OpenAI(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            max_retries=0,
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Connectivity check. Return exactly this token and no other text: "
                        f"{challenge}"
                    ),
                }
            ],
            temperature=0,
            max_tokens=64,
        )
    except AuthenticationError as exc:
        raise ProviderValidationError("provider rejected the credential") from exc
    except NotFoundError as exc:
        raise ProviderValidationError("provider endpoint or model was not found") from exc
    except APITimeoutError as exc:
        raise ProviderValidationError("provider request timed out") from exc
    except APIConnectionError as exc:
        raise ProviderValidationError("provider endpoint is unreachable") from exc
    except APIStatusError as exc:
        raise ProviderValidationError(
            f"provider request failed with HTTP {exc.status_code}"
        ) from exc
    except Exception as exc:
        raise ProviderValidationError("provider request failed") from exc
    finally:
        if client is not None:
            client.close()

    if len(response.choices) != 1:
        raise ProviderValidationError("provider response must contain one choice")
    content = response.choices[0].message.content
    if not isinstance(content, str) or content.strip() != challenge:
        raise ProviderValidationError("provider response failed the randomized challenge")

    request_id = str(response.id or "").strip()
    response_model = str(response.model or "").strip()
    if not request_id or not response_model:
        raise ProviderValidationError(
            "provider response is missing request id or model identity"
        )
    if api_key in request_id or api_key in response_model:
        raise ProviderValidationError(
            "provider response placed credential material in public metadata"
        )
    if response.usage is None:
        raise ProviderValidationError("provider response is missing usage metadata")
    input_tokens = _usage_value(response.usage, "prompt_tokens")
    output_tokens = _usage_value(response.usage, "completion_tokens")
    total_tokens = _usage_value(response.usage, "total_tokens")
    if total_tokens <= 0 or total_tokens < input_tokens + output_tokens:
        raise ProviderValidationError("provider usage totals are inconsistent")

    return ProviderValidationEvidence(
        response_model=response_model,
        upstream_request_id=request_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )
