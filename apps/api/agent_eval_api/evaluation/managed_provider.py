"""OpenAI-compatible execution for a platform-managed LLM Judge."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, cast

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)

from agent_eval_api.contracts import ExternalScoreProvenance, ScoreDirection, ScoreStatus

from .base import EvaluationContext, EvaluatorOutcome
from .judge import _rubric, _trace_payload

_PERMANENT_PROVIDER_ERRORS = (
    AuthenticationError,
    PermissionDeniedError,
    BadRequestError,
    NotFoundError,
)
_RETRYABLE_PROVIDER_ERRORS = (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
)


class ManagedJudgeError(RuntimeError):
    """A secret-safe permanent managed Judge failure."""

    error_type = "managed_judge_error"


class ManagedJudgeRetryableError(ManagedJudgeError):
    """A transient managed Judge failure that Celery may retry."""

    error_type = "managed_judge_retryable"


class _ChatCompletions(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class _Chat(Protocol):
    completions: _ChatCompletions


class ManagedJudgeClient(Protocol):
    chat: _Chat

    def close(self) -> None: ...


@dataclass(frozen=True)
class ManagedJudgeConfig:
    base_url: str
    model: str
    prompt_template: str
    output_schema: dict[str, Any]
    sampling_parameters: dict[str, Any]
    default_parameters: dict[str, Any]
    timeout_seconds: float = 60.0


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _render_prompt(context: EvaluationContext, template: str, rubric: str) -> str:
    values = {
        "metric_name": context.evaluator.name,
        "rubric": rubric,
        "input": context.case.input,
        "expected_output": context.case.expected_output,
        "actual_output": context.execution.output,
        "criteria": context.case.criteria,
        "tool_calls": [call.model_dump(mode="json") for call in context.execution.tool_calls],
        "trace": _trace_payload(context),
    }
    environment = SandboxedEnvironment(
        undefined=StrictUndefined,
        autoescape=False,
    )
    environment.filters["json"] = _json_text
    try:
        rendered = environment.from_string(template).render(**values)
    except TemplateError as exc:
        raise ManagedJudgeError("managed Judge prompt template is invalid") from exc
    if not rendered.strip():
        raise ManagedJudgeError("managed Judge prompt template rendered an empty prompt")
    return rendered


def _usage(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        raise ManagedJudgeError("managed Judge response is missing usage metadata")
    raw = usage.model_dump(mode="json") if hasattr(usage, "model_dump") else vars(usage)
    result = {key: value for key, value in dict(raw).items() if value is not None}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = result.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ManagedJudgeError(f"managed Judge response has invalid usage.{field}")
    if result["total_tokens"] < result["prompt_tokens"] + result["completion_tokens"]:
        raise ManagedJudgeError("managed Judge usage totals are inconsistent")
    return result


def _token_cost(
    usage: dict[str, Any], pricing: Any
) -> dict[str, Any] | None:
    if not isinstance(pricing, dict):
        return None
    try:
        input_rate = Decimal(str(pricing["input_per_million_tokens"]))
        output_rate = Decimal(str(pricing["output_per_million_tokens"]))
        cached_rate = Decimal(
            str(pricing.get("cached_input_per_million_tokens", input_rate))
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise ManagedJudgeError("managed Judge pricing configuration is invalid") from exc
    if min(input_rate, output_rate, cached_rate) < 0:
        raise ManagedJudgeError("managed Judge pricing rates cannot be negative")

    details = usage.get("prompt_tokens_details")
    cached_tokens = 0
    if isinstance(details, dict):
        candidate = details.get("cached_tokens", 0)
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
            cached_tokens = candidate
    cached_tokens = min(cached_tokens, int(usage["prompt_tokens"]))
    input_tokens = int(usage["prompt_tokens"]) - cached_tokens
    output_tokens = int(usage["completion_tokens"])
    total = (
        Decimal(input_tokens) * input_rate
        + Decimal(cached_tokens) * cached_rate
        + Decimal(output_tokens) * output_rate
    ) / Decimal(1_000_000)
    return {
        "amount": float(total),
        "currency": str(pricing.get("currency", "USD")),
        "calculation": "configured_token_rates",
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "output_tokens": output_tokens,
    }


def _passed(context: EvaluationContext, score: float, explicit: Any) -> bool | None:
    if explicit is not None:
        if not isinstance(explicit, bool):
            raise ManagedJudgeError("managed Judge passed field must be a boolean")
        return explicit
    threshold = context.evaluator.default_threshold
    if threshold is None:
        return None
    if context.evaluator.direction is ScoreDirection.LOWER_IS_BETTER:
        return score <= threshold
    return score >= threshold


def _response_content(response: Any) -> tuple[str, str | None]:
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or len(choices) != 1:
        raise ManagedJudgeError("managed Judge response must contain one choice")
    content = getattr(getattr(choices[0], "message", None), "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ManagedJudgeError("managed Judge response content is empty")
    finish_reason = getattr(choices[0], "finish_reason", None)
    return content, str(finish_reason) if finish_reason is not None else None


def evaluate_managed_judge(
    context: EvaluationContext,
    config: ManagedJudgeConfig,
    *,
    api_key: str,
    client: ManagedJudgeClient | None = None,
) -> EvaluatorOutcome:
    """Call a real provider once; Celery owns retries around this function."""

    rubric = _rubric(context)
    prompt = _render_prompt(context, config.prompt_template, rubric)
    owns_client = client is None
    if client is None:
        client = cast(
            ManagedJudgeClient,
            OpenAI(
                api_key=api_key,
                base_url=config.base_url.rstrip("/"),
                timeout=config.timeout_seconds,
                max_retries=0,
            ),
        )
    request_parameters = {
        key: value
        for key, value in config.default_parameters.items()
        if key not in {"pricing", "timeout_seconds"}
    }
    request_parameters.update(config.sampling_parameters)
    try:
        response = client.chat.completions.create(
            model=config.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an impartial evaluator. Return only one JSON object that "
                        "matches the supplied schema.\nRubric: "
                        + rubric
                        + "\nJSON Schema: "
                        + _json_text(config.output_schema)
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            stream=False,
            **request_parameters,
        )
    except _PERMANENT_PROVIDER_ERRORS as exc:
        raise ManagedJudgeError("managed Judge provider rejected the request") from exc
    except _RETRYABLE_PROVIDER_ERRORS as exc:
        raise ManagedJudgeRetryableError(
            "managed Judge provider is temporarily unavailable"
        ) from exc
    except APIStatusError as exc:
        if exc.status_code >= 500:
            raise ManagedJudgeRetryableError(
                "managed Judge provider is temporarily unavailable"
            ) from exc
        raise ManagedJudgeError(
            f"managed Judge provider returned HTTP {exc.status_code}"
        ) from exc
    finally:
        if owns_client:
            client.close()

    content, finish_reason = _response_content(response)
    try:
        structured = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ManagedJudgeError("managed Judge returned malformed JSON") from exc
    try:
        Draft202012Validator(config.output_schema).validate(structured)
    except ValidationError as exc:
        raise ManagedJudgeError(
            "managed Judge response does not match the evaluator output schema"
        ) from exc
    if not isinstance(structured, dict):
        raise ManagedJudgeError("managed Judge response must be a JSON object")
    score_value = structured.get("score")
    if not isinstance(score_value, (int, float)) or isinstance(score_value, bool):
        raise ManagedJudgeError("managed Judge score must be numeric")
    score = float(score_value)
    minimum = context.evaluator.score_min
    maximum = context.evaluator.score_max
    if minimum is not None and score < minimum:
        raise ManagedJudgeError("managed Judge score is below evaluator score_min")
    if maximum is not None and score > maximum:
        raise ManagedJudgeError("managed Judge score is above evaluator score_max")
    explanation = structured.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        raise ManagedJudgeError("managed Judge explanation must be a non-empty string")
    evidence = structured.get("evidence", [])
    if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
        raise ManagedJudgeError("managed Judge evidence must be an array of objects")
    label = structured.get("label")
    if label is not None and not isinstance(label, str):
        raise ManagedJudgeError("managed Judge label must be a string")
    passed = _passed(context, score, structured.get("passed"))
    status = (
        ScoreStatus.MISSING
        if passed is None
        else ScoreStatus.PASSED
        if passed
        else ScoreStatus.FAILED
    )
    response_usage = _usage(response)
    cost = _token_cost(response_usage, config.default_parameters.get("pricing"))
    response_model = str(getattr(response, "model", "") or config.model)
    request_id = str(getattr(response, "id", "") or "")
    provenance = ExternalScoreProvenance(
        source="platform_provider",
        protocol="openai_compatible_chat_completions",
        connection_id=context.evaluator.provider_connection_id,
        evaluator_version=f"{context.evaluator.name}@{context.evaluator.version}",
        model=response_model,
        rubric_version=context.evaluator.version,
        prompt_template_version=context.evaluator.version,
        metadata={
            "provider_request_id": request_id,
            "configured_model": config.model,
        },
    )
    raw_response = {
        "id": request_id,
        "model": response_model,
        "finish_reason": finish_reason,
        "content": structured,
        "usage": response_usage,
    }
    return EvaluatorOutcome(
        metric_name=context.evaluator.name,
        status=status,
        value=score,
        label=label,
        passed=passed,
        explanation=explanation,
        evidence=list(evidence),
        provenance=provenance,
        raw_response=raw_response,
        raw_result={
            "protocol": "openai_compatible_chat_completions",
            "provider": "platform_provider",
            "response": structured,
            "usage": response_usage,
            "cost": cost,
        },
        usage=response_usage,
        cost=cost,
    )
