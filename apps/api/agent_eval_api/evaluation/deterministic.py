"""Deterministic evaluators with explicit evidence and missing-result handling."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

from jsonschema import Draft202012Validator

from agent_eval_api.contracts import ExecutionStatus, ScoreDirection, ScoreStatus

from .base import (
    EvaluationContext,
    EvaluatorConfigurationError,
    EvaluatorOutcome,
)

EvaluatorFunction = Callable[[EvaluationContext], EvaluatorOutcome]


def _outcome(
    context: EvaluationContext,
    *,
    value: float,
    explanation: str,
    evidence: list[dict[str, Any]],
    threshold: float | None = None,
    raw_result: Any = None,
) -> EvaluatorOutcome:
    effective_threshold = threshold
    if effective_threshold is None:
        effective_threshold = context.evaluator.default_threshold
    if effective_threshold is None:
        effective_threshold = 1.0
    if context.evaluator.direction is ScoreDirection.LOWER_IS_BETTER:
        passed = value <= effective_threshold
    else:
        passed = value >= effective_threshold
    return EvaluatorOutcome(
        metric_name=context.evaluator.name,
        status=ScoreStatus.PASSED if passed else ScoreStatus.FAILED,
        value=value,
        passed=passed,
        explanation=explanation,
        evidence=evidence,
        raw_result=raw_result,
    )


def _missing(context: EvaluationContext, explanation: str) -> EvaluatorOutcome:
    return EvaluatorOutcome(
        metric_name=context.evaluator.name,
        status=ScoreStatus.MISSING,
        explanation=explanation,
    )


def _is_subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return False
        return all(
            key in actual and _is_subset(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            _is_subset(left, right) for left, right in zip(expected, actual, strict=True)
        )
    return bool(expected == actual)


def _read_path(value: Any, path: str | None) -> Any:
    if not path:
        return value
    current = value
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def _normalized_text(value: Any) -> str:
    return " ".join(str(value).split()).casefold()


def _context_item(value: Any) -> dict[str, Any] | None:
    """Normalize the small set of retrieval shapes used by external agents."""

    if isinstance(value, str) and value.strip():
        return {"content": value}
    if not isinstance(value, Mapping):
        return None
    nested_document = value.get("document")
    if isinstance(nested_document, Mapping):
        value = {
            **dict(nested_document),
            **{
            key: item for key, item in value.items() if key != "document"
            },
        }
    content = value.get("content") or value.get("text") or value.get("page_content")
    document_id = (
        value.get("document_id")
        or value.get("documentId")
        or value.get("source_id")
        or value.get("id")
    )
    if content is None and document_id is None:
        return None
    item: dict[str, Any] = {
        "content": str(content) if content is not None else "",
    }
    if document_id is not None:
        item["document_id"] = str(document_id)
    metadata = value.get("metadata")
    if isinstance(metadata, Mapping):
        item["metadata"] = dict(metadata)
    return item


def _context_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        items: list[dict[str, Any]] = []
        for item in value:
            normalized = _context_item(item)
            if normalized is not None:
                items.append(normalized)
                continue
            items.extend(_context_items(item))
        return items
    if isinstance(value, Mapping):
        for key in (
            "retrieval_context",
            "retrieved_contexts",
            "contexts",
            "documents",
            "results",
            "output",
            "data",
            "result",
        ):
            if key in value:
                return _context_items(value[key])
    normalized = _context_item(value)
    return [normalized] if normalized is not None else []


def _retrieved_contexts(context: EvaluationContext) -> list[dict[str, Any]]:
    if context.trace is None:
        return []
    candidates: list[dict[str, Any]] = []
    for span in context.trace.spans:
        if span.kind.value != "retrieval":
            continue
        candidates.extend(_context_items(span.output))
        for key in ("retrieval_context", "retrieved_contexts", "documents", "results"):
            candidates.extend(_context_items(span.attributes.get(key)))

    external = context.trace.extensions.get("agent_eval.external_trace")
    if isinstance(external, Mapping):
        candidates.extend(_context_items(external.get("retrieval_context")))
        candidates.extend(_context_items(external.get("retrieved_contexts")))
        candidates.extend(_context_items(external.get("spans")))

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        identity = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
        if identity not in seen:
            seen.add(identity)
            unique.append(item)
    return unique


def context_recall(context: EvaluationContext) -> EvaluatorOutcome:
    """Measure how many expected reference documents were retrieved."""

    expected = [item.model_dump(mode="json") for item in context.case.retrieval_context]
    if not expected:
        return _missing(context, "retrieval_context is required")
    if context.trace is None:
        return _missing(context, "trace retrieval evidence is required")
    actual = _retrieved_contexts(context)
    if not actual:
        return _missing(context, "trace contains no retrieval evidence")

    matched: list[dict[str, Any]] = []
    for expected_item in expected:
        expected_id = expected_item.get("document_id")
        expected_content = _normalized_text(expected_item.get("content", ""))
        matching = next(
            (
                item
                for item in actual
                if (
                    expected_id is not None
                    and item.get("document_id") == expected_id
                )
                or (
                    expected_id is None
                    and expected_content
                    and expected_content in _normalized_text(item.get("content", ""))
                )
            ),
            None,
        )
        matched.append(
            {
                "expected": expected_item,
                "actual": matching,
                "matched": matching is not None,
            }
        )
    value = sum(item["matched"] for item in matched) / len(expected)
    return _outcome(
        context,
        value=value,
        explanation=(
            f"{sum(item['matched'] for item in matched)}/{len(expected)} "
            "reference contexts retrieved"
        ),
        evidence=[
            {
                "expected_count": len(expected),
                "actual_count": len(actual),
                "matches": matched,
            }
        ],
    )


def _citation_ids(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        value = value.get("citations", value.get("references", value.get("sources")))
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    citations: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            item = item.get("document_id") or item.get("documentId") or item.get("id")
        if item is not None and str(item).strip():
            citations.append(str(item))
    return citations


def citation_accuracy(context: EvaluationContext) -> EvaluatorOutcome:
    expected = {
        item.document_id for item in context.case.retrieval_context if item.document_id
    }
    if not expected:
        return _missing(context, "retrieval_context document_id values are required")
    path = str(context.evaluator.config.get("citation_path", "citations"))
    citations = _citation_ids(_read_path(context.execution.output, path))
    actual = set(citations)
    true_positives = len(expected & actual)
    precision = true_positives / len(actual) if actual else 0.0
    recall = true_positives / len(expected)
    mode = str(context.evaluator.config.get("citation_metric", "f1")).casefold()
    if mode == "precision":
        value = precision
    elif mode == "recall":
        value = recall
    elif mode == "f1":
        value = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    else:
        raise EvaluatorConfigurationError(
            "citation_metric must be precision, recall or f1"
        )
    return _outcome(
        context,
        value=value,
        explanation=f"citation {mode} is {value:.3f}",
        evidence=[
            {
                "expected_citations": sorted(expected),
                "actual_citations": citations,
                "true_positives": true_positives,
                "precision": precision,
                "recall": recall,
                "metric": mode,
                "path": path,
            }
        ],
    )


def output_format(context: EvaluationContext) -> EvaluatorOutcome:
    """Check a declared output shape without depending on a model provider."""

    config = context.evaluator.config
    actual = _read_path(
        context.execution.output,
        str(config["output_path"]) if config.get("output_path") else None,
    )
    format_name = str(config.get("format", config.get("type", ""))).casefold()
    aliases = {
        "json_object": "object",
        "json_array": "array",
        "text": "string",
        "int": "integer",
        "float": "number",
    }
    format_name = aliases.get(format_name, format_name)
    if not format_name:
        if context.case.output_schema is None:
            return _missing(context, "output_format requires config.format or output_schema")
        schema = context.case.output_schema
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            raise EvaluatorConfigurationError(f"invalid output_schema: {exc}") from exc
        errors = list(Draft202012Validator(schema).iter_errors(actual))
        return _outcome(
            context,
            value=0.0 if errors else 1.0,
            explanation=(
                "output matches the declared output schema"
                if not errors
                else f"{len(errors)} output schema errors"
            ),
            evidence=[
                {
                    "format": "json_schema",
                    "errors": [error.message for error in errors],
                }
            ],
        )

    valid_formats = {
        "json",
        "object",
        "array",
        "string",
        "number",
        "integer",
        "boolean",
        "non_empty",
    }
    if format_name not in valid_formats:
        raise EvaluatorConfigurationError(
            "output format must be json, object, array, string, number, integer, "
            "boolean or non_empty"
        )
    if format_name == "json":
        try:
            json.dumps(actual, ensure_ascii=False)
            matches = True
        except (TypeError, ValueError):
            matches = False
    elif format_name == "object":
        matches = isinstance(actual, Mapping)
    elif format_name == "array":
        matches = isinstance(actual, list)
    elif format_name == "string":
        matches = isinstance(actual, str)
    elif format_name == "number":
        matches = isinstance(actual, int | float) and not isinstance(actual, bool)
    elif format_name == "integer":
        matches = isinstance(actual, int) and not isinstance(actual, bool)
    elif format_name == "boolean":
        matches = isinstance(actual, bool)
    else:
        matches = actual is not None and bool(actual)

    required_fields = config.get("required_fields", [])
    missing_fields: list[str] = []
    if required_fields:
        if not isinstance(required_fields, list) or not isinstance(actual, Mapping):
            missing_fields = [str(field) for field in required_fields]
        else:
            missing_fields = [
                str(field) for field in required_fields if str(field) not in actual
            ]
        matches = matches and not missing_fields

    pattern = config.get("pattern")
    if pattern is not None:
        if not isinstance(actual, str):
            pattern_matches = False
        else:
            try:
                pattern_matches = re.fullmatch(str(pattern), actual) is not None
            except re.error as exc:
                raise EvaluatorConfigurationError("output format pattern is invalid") from exc
        matches = matches and pattern_matches
    min_length = config.get("min_length")
    if min_length is not None:
        if not isinstance(min_length, int) or min_length < 0:
            raise EvaluatorConfigurationError(
                "output format min_length must be a non-negative integer"
            )
        try:
            matches = matches and len(actual) >= min_length
        except TypeError:
            matches = False
    return _outcome(
        context,
        value=1.0 if matches else 0.0,
        explanation=(
            f"output format is {format_name}"
            if matches
            else f"output format is not {format_name}"
        ),
        evidence=[
            {
                "format": format_name,
                "output_path": config.get("output_path"),
                "required_fields": required_fields,
                "missing_fields": missing_fields,
                "pattern": pattern,
                "min_length": min_length,
                "actual_type": type(actual).__name__,
            }
        ],
    )


def error_rate(context: EvaluationContext) -> EvaluatorOutcome:
    """Return 1 for an observed execution error and 0 for a clean execution."""

    if context.execution.status in {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}:
        return EvaluatorOutcome(
            metric_name=context.evaluator.name,
            status=ScoreStatus.NOT_RUN,
            explanation="execution has not reached a terminal state",
        )
    sources: list[dict[str, Any]] = []
    if context.execution.status in {ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}:
        sources.append(
            {
                "source": "execution",
                "status": context.execution.status.value,
                "error_type": context.execution.error_type,
                "error_message": context.execution.error_message,
            }
        )
    if context.trace is not None:
        if context.trace.status in {ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}:
            sources.append({"source": "trace", "status": context.trace.status.value})
        for span in context.trace.spans:
            if span.error is not None or span.status in {
                ExecutionStatus.FAILED,
                ExecutionStatus.CANCELLED,
            }:
                sources.append(
                    {
                        "source": "span",
                        "span_id": span.span_id,
                        "name": span.name,
                        "status": span.status.value,
                        "error": span.error,
                    }
                )
    has_error = bool(sources)
    return _outcome(
        context,
        value=1.0 if has_error else 0.0,
        explanation="execution errors detected" if has_error else "no execution errors detected",
        evidence=sources or [{"error": False}],
    )


def task_success(context: EvaluationContext) -> EvaluatorOutcome:
    expected = context.case.expected_state
    if expected is None:
        return _missing(context, "expected_state is required")
    raw_path = context.evaluator.config.get("actual_state_path")
    path = str(raw_path) if raw_path is not None else None
    actual = _read_path(context.execution.output, path)
    passed = _is_subset(expected, actual)
    return _outcome(
        context,
        value=1.0 if passed else 0.0,
        explanation=(
            "actual state contains the expected state" if passed else "actual state differs"
        ),
        evidence=[{"expected_state": expected, "actual_state": actual, "path": path}],
    )


def exact_match(context: EvaluationContext) -> EvaluatorOutcome:
    expected = context.case.expected_output
    if expected is None:
        return _missing(context, "expected_output is required")
    actual = context.execution.output
    normalize_whitespace = bool(context.evaluator.config.get("normalize_whitespace", False))
    case_sensitive = bool(context.evaluator.config.get("case_sensitive", True))
    compared_expected = expected
    compared_actual = actual
    if isinstance(expected, str) and isinstance(actual, str):
        expected_text = expected
        actual_text = actual
        if normalize_whitespace:
            expected_text = " ".join(expected_text.split())
            actual_text = " ".join(actual_text.split())
        if not case_sensitive:
            expected_text = expected_text.casefold()
            actual_text = actual_text.casefold()
        compared_expected = expected_text
        compared_actual = actual_text
    matched = bool(compared_expected == compared_actual)
    return _outcome(
        context,
        value=1.0 if matched else 0.0,
        explanation="output exactly matches the reference" if matched else "output differs",
        evidence=[
            {
                "expected_output": expected,
                "actual_output": actual,
                "normalize_whitespace": normalize_whitespace,
                "case_sensitive": case_sensitive,
            }
        ],
    )


def tool_correctness(context: EvaluationContext) -> EvaluatorOutcome:
    expected_names = [call.name for call in context.case.expected_tools]
    actual_names = [call.name for call in context.execution.tool_calls]
    if not expected_names and not actual_names:
        score = 1.0
    else:
        overlap = sum((Counter(expected_names) & Counter(actual_names)).values())
        precision = overlap / len(actual_names) if actual_names else 0.0
        recall = overlap / len(expected_names) if expected_names else 0.0
        score = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    ordered = bool(context.evaluator.config.get("ordered", False))
    order_matches = not ordered or expected_names == actual_names
    if not order_matches:
        score = 0.0
    return _outcome(
        context,
        value=score,
        explanation="tool selection matches" if score == 1.0 else "tool selection differs",
        evidence=[
            {
                "expected_tools": expected_names,
                "actual_tools": actual_names,
                "ordered": ordered,
                "order_matches": order_matches,
            }
        ],
    )


def argument_correctness(context: EvaluationContext) -> EvaluatorOutcome:
    expected_calls = context.case.expected_tools
    if not expected_calls:
        return _missing(context, "expected_tools is required")
    actual_calls = context.execution.tool_calls
    used: set[int] = set()
    checks: list[dict[str, Any]] = []
    correct = 0
    for expected in expected_calls:
        candidate_index = next(
            (
                index
                for index, actual in enumerate(actual_calls)
                if index not in used
                and actual.name == expected.name
                and (expected.order is None or actual.order == expected.order)
            ),
            None,
        )
        actual_arguments = None
        matched = False
        if candidate_index is not None:
            used.add(candidate_index)
            actual_arguments = actual_calls[candidate_index].arguments
            matched = _is_subset(expected.arguments, actual_arguments)
        correct += int(matched)
        checks.append(
            {
                "tool": expected.name,
                "expected_arguments": expected.arguments,
                "actual_arguments": actual_arguments,
                "matched": matched,
            }
        )
    score = correct / len(expected_calls)
    return _outcome(
        context,
        value=score,
        explanation=f"{correct}/{len(expected_calls)} expected tool arguments match",
        evidence=checks,
    )


def policy_compliance(context: EvaluationContext) -> EvaluatorOutcome:
    config = context.evaluator.config
    supported_rules = {
        "forbidden_tools",
        "required_tools",
        "max_tool_calls",
        "forbidden_output_patterns",
    }
    if not supported_rules.intersection(config):
        return _missing(context, "no deterministic policy rules are configured")
    actual_tools = [call.name for call in context.execution.tool_calls]
    violations: list[dict[str, Any]] = []
    for tool in config.get("forbidden_tools", []):
        if tool in actual_tools:
            violations.append({"rule": "forbidden_tool", "tool": tool})
    for tool in config.get("required_tools", []):
        if tool not in actual_tools:
            violations.append({"rule": "required_tool_missing", "tool": tool})
    max_tool_calls = config.get("max_tool_calls")
    if isinstance(max_tool_calls, int) and len(actual_tools) > max_tool_calls:
        violations.append(
            {"rule": "max_tool_calls", "limit": max_tool_calls, "actual": len(actual_tools)}
        )
    serialized_output = json.dumps(context.execution.output, ensure_ascii=False).casefold()
    for pattern in config.get("forbidden_output_patterns", []):
        if str(pattern).casefold() in serialized_output:
            violations.append({"rule": "forbidden_output_pattern", "pattern": pattern})
    return _outcome(
        context,
        value=0.0 if violations else 1.0,
        explanation="policy violations found" if violations else "all deterministic rules passed",
        evidence=violations or [{"rules_checked": sorted(supported_rules.intersection(config))}],
        raw_result={"violations": violations},
    )


def json_schema(context: EvaluationContext) -> EvaluatorOutcome:
    schema = context.case.output_schema
    if schema is None:
        return _missing(context, "output_schema is required")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise EvaluatorConfigurationError(f"invalid output_schema: {exc}") from exc
    errors = sorted(
        Draft202012Validator(schema).iter_errors(context.execution.output),
        key=lambda error: list(error.absolute_path),
    )
    evidence = [
        {
            "message": error.message,
            "instance_path": list(error.absolute_path),
            "schema_path": list(error.absolute_schema_path),
        }
        for error in errors
    ]
    return _outcome(
        context,
        value=0.0 if errors else 1.0,
        explanation="output matches JSON Schema" if not errors else f"{len(errors)} schema errors",
        evidence=evidence or [{"valid": True}],
    )


def latency(context: EvaluationContext) -> EvaluatorOutcome:
    started_at = context.execution.started_at
    finished_at = context.execution.finished_at
    if started_at is None or finished_at is None:
        return _missing(context, "execution timestamps are required")
    latency_ms = max(0.0, (finished_at - started_at).total_seconds() * 1000)
    raw_limit = context.evaluator.config.get("max_ms")
    limit = float(raw_limit) if raw_limit is not None else context.evaluator.default_threshold
    if limit is None:
        raise EvaluatorConfigurationError("latency requires config.max_ms or default_threshold")
    return _outcome(
        context,
        value=latency_ms,
        threshold=limit,
        explanation=f"latency is {latency_ms:.3f} ms (limit {limit:.3f} ms)",
        evidence=[{"latency_ms": latency_ms, "max_ms": limit}],
    )


def cost(context: EvaluationContext) -> EvaluatorOutcome:
    raw_cost = context.execution.usage.get("cost")
    if raw_cost is None:
        return _missing(context, "usage.cost is required")
    try:
        value = float(raw_cost)
    except (TypeError, ValueError) as exc:
        raise EvaluatorConfigurationError("usage.cost must be numeric") from exc
    raw_limit = context.evaluator.config.get("max_cost")
    limit = float(raw_limit) if raw_limit is not None else context.evaluator.default_threshold
    if limit is None:
        raise EvaluatorConfigurationError("cost requires config.max_cost or default_threshold")
    return _outcome(
        context,
        value=value,
        threshold=limit,
        explanation=f"cost is {value:.6f} (limit {limit:.6f})",
        evidence=[{"cost": value, "max_cost": limit}],
    )


def token_usage(context: EvaluationContext) -> EvaluatorOutcome:
    field = str(context.evaluator.config.get("token_field", "total_tokens"))
    raw_value = context.execution.usage.get(field)
    if raw_value is None and field == "total_tokens":
        input_tokens = context.execution.usage.get("input_tokens")
        output_tokens = context.execution.usage.get("output_tokens")
        if input_tokens is not None and output_tokens is not None:
            raw_value = int(input_tokens) + int(output_tokens)
    if raw_value is None:
        return _missing(context, f"usage.{field} is required")
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise EvaluatorConfigurationError(f"usage.{field} must be numeric") from exc
    raw_limit = context.evaluator.config.get("max_tokens")
    limit = float(raw_limit) if raw_limit is not None else context.evaluator.default_threshold
    if limit is None:
        raise EvaluatorConfigurationError("token usage requires max_tokens or default_threshold")
    return _outcome(
        context,
        value=value,
        threshold=limit,
        explanation=f"{field} is {value:.0f} (limit {limit:.0f})",
        evidence=[{"token_field": field, "tokens": value, "max_tokens": limit}],
    )


DETERMINISTIC_EVALUATORS: dict[str, EvaluatorFunction] = {
    "exact_match": exact_match,
    "task_success": task_success,
    "state_assertion": task_success,
    "state": task_success,
    "tool_correctness": tool_correctness,
    "argument_correctness": argument_correctness,
    "policy_compliance": policy_compliance,
    "json_schema": json_schema,
    "output_format": output_format,
    "context_recall": context_recall,
    "retrieval_context": context_recall,
    "reference_context": context_recall,
    "citation": citation_accuracy,
    "citation_accuracy": citation_accuracy,
    "latency": latency,
    "token": token_usage,
    "token_usage": token_usage,
    "cost": cost,
    "error": error_rate,
    "error_rate": error_rate,
    "execution_error_rate": error_rate,
}


def deterministic_evaluator_key(name: str) -> str:
    key = name.casefold().replace("-", "_").replace(" ", "_")
    for prefix in ("prompt_", "rag_", "tool_"):
        unprefixed = key.removeprefix(prefix)
        if unprefixed in DETERMINISTIC_EVALUATORS:
            return unprefixed
    return key


def evaluate_deterministic(context: EvaluationContext) -> list[EvaluatorOutcome]:
    configured_metric = context.evaluator.config.get("metric", context.evaluator.name)
    evaluator = DETERMINISTIC_EVALUATORS.get(deterministic_evaluator_key(str(configured_metric)))
    if evaluator is None:
        raise EvaluatorConfigurationError(
            f"unknown deterministic evaluator: {context.evaluator.name}"
        )
    return [evaluator(context)]
