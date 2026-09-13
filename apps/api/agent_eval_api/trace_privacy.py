"""Trace payload redaction and bounded-content references."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from agent_eval_api.contracts import Trace, TraceSpan
from agent_eval_api.settings import Settings

REDACTED_VALUE: dict[str, bool] = {"__agent_eval_redacted": True}
_CREDENTIAL_VALUE = re.compile(
    r"(?i)(?:\b(?:bearer|basic)\s+[a-z0-9._~+/-]+=*|"
    r"\b(?:sk|rk|pk|ghp|xox[baprs])-[a-z0-9_-]{8,}|"
    r"\b(?:api[_-]?key|token|password|secret)\b\s*[:=]\s*[^,\s}]+)"
)


@dataclass
class PrivacyStats:
    redacted_fields: int = 0
    truncated_fields: int = 0


def normalize_key(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def is_sensitive_key(key: str, settings: Settings) -> bool:
    normalized = normalize_key(key)
    return any(
        normalize_key(sensitive_name) in normalized
        for sensitive_name in settings.trace_redaction_field_names
    )


def content_size(value: Any) -> int:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return len(serialized.encode())


def content_reference(value: Any) -> dict[str, Any]:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "__agent_eval_content_ref": {
            "kind": "truncated",
            "sha256": hashlib.sha256(serialized.encode()).hexdigest(),
            "original_bytes": len(serialized.encode()),
        }
    }


def sanitize_value(
    value: Any,
    settings: Settings,
    stats: PrivacyStats,
    *,
    redact_keys: bool = True,
) -> Any:
    # Persistence and presentation can both pass through this function. Keep
    # our own markers opaque so a second pass cannot alter their meaning.
    if isinstance(value, dict):
        if value.get("__agent_eval_redacted") is True:
            return REDACTED_VALUE.copy()
        if set(value) == {"__agent_eval_content_ref"}:
            return {"__agent_eval_content_ref": value["__agent_eval_content_ref"]}

    sanitized: Any
    if isinstance(value, dict):
        sanitized = {
            key: (
                mark_redacted(stats)
                if redact_keys and is_sensitive_key(str(key), settings)
                else sanitize_value(item, settings, stats, redact_keys=redact_keys)
            )
            for key, item in value.items()
        }
    elif isinstance(value, list):
        sanitized = [
            sanitize_value(item, settings, stats, redact_keys=redact_keys) for item in value
        ]
    elif isinstance(value, str) and _CREDENTIAL_VALUE.search(value):
        return mark_redacted(stats)
    else:
        sanitized = value

    if content_size(sanitized) > settings.trace_max_field_bytes:
        stats.truncated_fields += 1
        return content_reference(sanitized)
    return sanitized


def mark_redacted(stats: PrivacyStats) -> dict[str, bool]:
    stats.redacted_fields += 1
    return REDACTED_VALUE.copy()


def sanitize_trace(trace: Trace, settings: Settings) -> Trace:
    """Return a trace safe to persist and expose through normal API responses."""

    stats = PrivacyStats()
    existing_privacy = trace.extensions.get("agent_eval.privacy")
    if isinstance(existing_privacy, dict):
        stats.redacted_fields = int(existing_privacy.get("redacted_fields", 0) or 0)
        stats.truncated_fields = int(existing_privacy.get("truncated_fields", 0) or 0)
    spans = [
        TraceSpan(
            span_id=span.span_id,
            trace_id=span.trace_id,
            parent_span_id=span.parent_span_id,
            kind=span.kind,
            name=span.name,
            status=span.status,
            started_at=span.started_at,
            ended_at=span.ended_at,
            input=sanitize_value(span.input, settings, stats),
            output=sanitize_value(span.output, settings, stats),
            error=sanitize_value(span.error, settings, stats),
            # Metric keys such as input_tokens contain the word "token" but are
            # measurement data, not credential fields.
            usage=sanitize_value(span.usage, settings, stats, redact_keys=False),
            cost=sanitize_value(span.cost, settings, stats, redact_keys=False),
            attributes=sanitize_value(span.attributes, settings, stats),
            extensions=sanitize_value(span.extensions, settings, stats),
        )
        for span in trace.spans
    ]
    extensions = sanitize_value(trace.extensions, settings, stats)
    assert isinstance(extensions, dict)
    extensions["agent_eval.privacy"] = {
        "redacted_fields": stats.redacted_fields,
        "truncated_fields": stats.truncated_fields,
    }
    return Trace(
        trace_id=trace.trace_id,
        run_id=trace.run_id,
        case_id=trace.case_id,
        status=trace.status,
        spans=spans,
        scores=trace.scores,
        source=trace.source,
        extensions=extensions,
    )
