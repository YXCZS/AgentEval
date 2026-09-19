"""Fail-closed preflight for the real OpenAI-compatible acceptance provider."""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv
from openai import OpenAI


class LiveProviderConfigurationError(ValueError):
    """The live provider environment is incomplete or unsafe."""


class LiveProviderPreflightError(RuntimeError):
    """The configured provider did not produce verifiable live evidence."""


@dataclass(frozen=True)
class LiveProviderConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> LiveProviderConfig:
        base_url = os.getenv("LIVE_ACCEPTANCE_BASE_URL", "").strip()
        api_key = os.getenv("LIVE_ACCEPTANCE_API_KEY", "").strip()
        model = os.getenv("LIVE_ACCEPTANCE_CHAT_MODEL", "").strip()
        timeout_raw = os.getenv("LIVE_ACCEPTANCE_TIMEOUT_SECONDS", "30").strip()
        missing = [
            name
            for name, value in (
                ("LIVE_ACCEPTANCE_BASE_URL", base_url),
                ("LIVE_ACCEPTANCE_API_KEY", api_key),
                ("LIVE_ACCEPTANCE_CHAT_MODEL", model),
            )
            if not value
        ]
        if missing:
            raise LiveProviderConfigurationError(
                "missing required live acceptance variables: " + ", ".join(missing)
            )
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError as exc:
            raise LiveProviderConfigurationError(
                "LIVE_ACCEPTANCE_TIMEOUT_SECONDS must be a number"
            ) from exc
        if timeout_seconds <= 0:
            raise LiveProviderConfigurationError(
                "LIVE_ACCEPTANCE_TIMEOUT_SECONDS must be greater than zero"
            )
        _validate_base_url(base_url)
        return cls(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )


@dataclass(frozen=True)
class LiveEmbeddingConfig:
    """A separately configured OpenAI-compatible embedding Provider."""

    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> LiveEmbeddingConfig:
        # The concise EMBEDDING_* names are supported for local configurations.
        # Acceptance-specific names win when both are supplied.
        base_url = _environment_value(
            "LIVE_ACCEPTANCE_EMBEDDING_BASE_URL", "EMBEDDING_BASE_URL"
        )
        api_key = _environment_value(
            "LIVE_ACCEPTANCE_EMBEDDING_API_KEY", "EMBEDDING_API_KEY"
        )
        model = _environment_value(
            "LIVE_ACCEPTANCE_EMBEDDING_MODEL", "EMBEDDING_MODEL"
        )
        timeout_raw = os.getenv("LIVE_ACCEPTANCE_TIMEOUT_SECONDS", "30").strip()
        missing = [
            name
            for name, value in (
                (
                    "LIVE_ACCEPTANCE_EMBEDDING_BASE_URL or EMBEDDING_BASE_URL",
                    base_url,
                ),
                (
                    "LIVE_ACCEPTANCE_EMBEDDING_API_KEY or EMBEDDING_API_KEY",
                    api_key,
                ),
                (
                    "LIVE_ACCEPTANCE_EMBEDDING_MODEL or EMBEDDING_MODEL",
                    model,
                ),
            )
            if not value
        ]
        if missing:
            raise LiveProviderConfigurationError(
                "missing required live acceptance variables: " + ", ".join(missing)
            )
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError as exc:
            raise LiveProviderConfigurationError(
                "LIVE_ACCEPTANCE_TIMEOUT_SECONDS must be a number"
            ) from exc
        if timeout_seconds <= 0:
            raise LiveProviderConfigurationError(
                "LIVE_ACCEPTANCE_TIMEOUT_SECONDS must be greater than zero"
            )
        _validate_base_url(base_url)
        return cls(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )


def _environment_value(primary_name: str, fallback_name: str) -> str:
    return os.getenv(primary_name, "").strip() or os.getenv(fallback_name, "").strip()


@dataclass(frozen=True)
class LiveProviderEvidence:
    endpoint_origin: str
    configured_model: str
    response_model: str
    upstream_request_id: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    challenge_verified: bool


@dataclass(frozen=True)
class LiveEmbeddingEvidence:
    endpoint_origin: str
    configured_model: str
    response_model: str
    vector_dimensions: int
    input_tokens: int


def _validate_base_url(base_url: str) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LiveProviderConfigurationError(
            "LIVE_ACCEPTANCE_BASE_URL must be an absolute HTTP(S) URL"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LiveProviderConfigurationError(
            "LIVE_ACCEPTANCE_BASE_URL must not contain credentials, query, or fragment"
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise LiveProviderConfigurationError(
            "LIVE_ACCEPTANCE_BASE_URL contains an invalid port"
        ) from exc


def _endpoint_origin(base_url: str) -> str:
    parsed = urlsplit(base_url)
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme}://{host}{port}"


def _safe_error(exc: Exception, api_key: str) -> str:
    message = str(exc).replace(api_key, "[REDACTED]")
    message = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
        r"\1[REDACTED]",
        message,
    )
    return message[:500]


def _usage_value(usage: Any, name: str) -> int:
    value = getattr(usage, name, None)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LiveProviderPreflightError(
            f"upstream response is missing valid usage.{name}"
        )
    return value


def _embedding_usage(usage: Any) -> int:
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    # Some OpenAI-compatible embedding providers (e.g. Alibaba Bailian) return
    # total_tokens but omit prompt_tokens. Fall back to total_tokens.
    if isinstance(prompt_tokens, int) and not isinstance(prompt_tokens, bool) and prompt_tokens > 0:
        return prompt_tokens
    if isinstance(total_tokens, int) and not isinstance(total_tokens, bool) and total_tokens > 0:
        return total_tokens
    raise LiveProviderPreflightError("embedding response is missing valid usage token counts")


def run_preflight(config: LiveProviderConfig) -> LiveProviderEvidence:
    """Call the real provider with a random challenge and validate its evidence."""

    challenge = f"AGENT_EVAL_{secrets.token_hex(12)}"
    client = OpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout_seconds,
        max_retries=0,
    )
    try:
        response = client.chat.completions.create(
            model=config.model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "This is a connectivity check. Return this random token exactly once "
                        f"and no other text: {challenge}"
                    ),
                }
            ],
        )
    except Exception as exc:
        raise LiveProviderPreflightError(
            "real provider request failed: " + _safe_error(exc, config.api_key)
        ) from exc
    finally:
        client.close()

    if not response.choices:
        raise LiveProviderPreflightError("upstream response contains no choices")
    content = response.choices[0].message.content
    if not isinstance(content, str) or challenge not in content:
        raise LiveProviderPreflightError(
            "upstream response did not return the randomized challenge"
        )
    request_id = str(response.id or "").strip()
    response_model = str(response.model or "").strip()
    if not request_id or not response_model:
        raise LiveProviderPreflightError(
            "upstream response is missing request id or model identity"
        )
    if config.api_key in request_id or config.api_key in response_model:
        raise LiveProviderPreflightError(
            "upstream response placed credential material in public metadata"
        )
    if response.usage is None:
        raise LiveProviderPreflightError("upstream response is missing usage metadata")
    input_tokens = _usage_value(response.usage, "prompt_tokens")
    output_tokens = _usage_value(response.usage, "completion_tokens")
    total_tokens = _usage_value(response.usage, "total_tokens")
    if total_tokens <= 0 or total_tokens < input_tokens + output_tokens:
        raise LiveProviderPreflightError("upstream usage totals are inconsistent")

    return LiveProviderEvidence(
        endpoint_origin=_endpoint_origin(config.base_url),
        configured_model=config.model,
        response_model=response_model,
        upstream_request_id=request_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        challenge_verified=True,
    )


def run_embedding_preflight(config: LiveEmbeddingConfig) -> LiveEmbeddingEvidence:
    """Prove the configured embedding endpoint works before creating platform data."""

    challenge = f"AGENT_EVAL_EMBEDDING_{secrets.token_hex(12)}"
    client = OpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout_seconds,
        max_retries=0,
    )
    try:
        response = client.embeddings.create(model=config.model, input=challenge)
    except Exception as exc:
        raise LiveProviderPreflightError(
            "real embedding provider request failed: " + _safe_error(exc, config.api_key)
        ) from exc
    finally:
        client.close()
    data = list(getattr(response, "data", []) or [])
    if len(data) != 1 or int(getattr(data[0], "index", -1)) != 0:
        raise LiveProviderPreflightError("embedding response has no valid first vector")
    vector = getattr(data[0], "embedding", None)
    if not isinstance(vector, list) or not vector or not all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in vector
    ):
        raise LiveProviderPreflightError("embedding response has no numeric vector")
    response_model = str(getattr(response, "model", "") or "").strip()
    if not response_model:
        raise LiveProviderPreflightError("embedding response is missing model identity")
    if config.api_key in response_model:
        raise LiveProviderPreflightError(
            "embedding response placed credential material in public metadata"
        )
    input_tokens = _embedding_usage(getattr(response, "usage", None))
    if input_tokens <= 0:
        raise LiveProviderPreflightError("embedding response usage.prompt_tokens must be positive")
    return LiveEmbeddingEvidence(
        endpoint_origin=_endpoint_origin(config.base_url),
        configured_model=config.model,
        response_model=response_model,
        vector_dimensions=len(vector),
        input_tokens=input_tokens,
    )


def main() -> int:
    load_dotenv(override=False)
    try:
        evidence = run_preflight(LiveProviderConfig.from_environment())
    except LiveProviderConfigurationError as exc:
        print(f"live provider configuration error: {exc}", file=sys.stderr)
        return 2
    except LiveProviderPreflightError as exc:
        print(f"live provider preflight failed: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(asdict(evidence), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
