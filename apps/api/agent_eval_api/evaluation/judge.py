"""Evaluation adapter for a user-managed external LLM Judge."""

from __future__ import annotations

from typing import Any

import httpx

from agent_eval_api.contracts import (
    ExternalJudgeRequest,
    ScoreDirection,
    ScoreStatus,
)

from .base import EvaluationContext, EvaluatorConfigurationError, EvaluatorOutcome
from .external_protocols import (
    ExternalJudgeConfig,
    ExternalProtocolError,
    call_external_judge,
)

DEFAULT_RUBRICS = {
    "answer_quality": (
        "Score how correct, relevant, clear, and useful the actual answer is for the input and "
        "reference answer. Do not reward unsupported claims."
    ),
    "instruction_following": (
        "Score whether the answer follows all explicit instructions and criteria in the test case."
    ),
    "completeness": (
        "Score whether the answer covers every material part needed to satisfy the request."
    ),
    "natural_language_rules": (
        "Score whether the output complies with every natural-language rule in criteria."
    ),
}


def _metric_key(name: str) -> str:
    key = name.casefold().replace("-", "_").replace(" ", "_")
    aliases = {"rule_compliance": "natural_language_rules", "answer_correctness": "answer_quality"}
    return aliases.get(key, key)


def _rubric(context: EvaluationContext) -> str:
    key = _metric_key(context.evaluator.name)
    configured = context.evaluator.rubric
    if configured:
        return configured
    if key in {"instruction_following", "natural_language_rules"} and not context.case.criteria:
        raise EvaluatorConfigurationError("criteria or an evaluator rubric is required")
    return DEFAULT_RUBRICS.get(
        key,
        "Evaluate the actual Agent result against the input, expected output, and criteria. "
        "Return a score supported by concise evidence.",
    )


def _trace_payload(context: EvaluationContext) -> dict[str, Any] | None:
    if context.trace is None:
        return None
    return context.trace.model_dump(mode="json", exclude_none=True)


def _request(context: EvaluationContext, rubric: str) -> ExternalJudgeRequest:
    return ExternalJudgeRequest(
        run_id=context.execution.run_id,
        case_id=context.execution.case_id,
        trace_id=context.execution.trace_id,
        metric_name=context.evaluator.name,
        evaluator_version=f"{context.evaluator.name}@{context.evaluator.version}",
        rubric=rubric,
        input=context.case.input,
        expected_output=context.case.expected_output,
        actual_output=context.execution.output,
        criteria=context.case.criteria,
        tool_calls=list(context.execution.tool_calls),
        trace=_trace_payload(context),
        metadata={
            "source": "offline-experiment",
            "agent_type": context.evaluator.supported_agent_types[0].value,
        },
    )


def _passed(context: EvaluationContext, score: float, explicit: bool | None) -> bool | None:
    if explicit is not None:
        return explicit
    threshold = context.evaluator.default_threshold
    if threshold is None:
        return None
    if context.evaluator.direction is ScoreDirection.LOWER_IS_BETTER:
        return score <= threshold
    return score >= threshold


async def evaluate_llm_judge(
    context: EvaluationContext,
    config: ExternalJudgeConfig,
    *,
    signing_secret: str,
    client: httpx.AsyncClient | None = None,
) -> list[EvaluatorOutcome]:
    """Call the external Judge protocol and normalize its response.

    The signing secret is deliberately an invocation-only argument. It is never
    read from platform model settings and is not included in the returned raw
    result.
    """

    rubric = _rubric(context)
    result = await call_external_judge(
        config,
        _request(context, rubric),
        signing_secret=signing_secret,
        client=client,
    )
    response = result.response
    provenance = response.provenance.model_copy(
        update={
            "source": "external_judge",
            "protocol": "signed_http_json_v1",
            "connection_id": config.connection_id,
        }
    )
    minimum = context.evaluator.score_min
    maximum = context.evaluator.score_max
    if minimum is not None and response.score < minimum:
        raise ExternalProtocolError(
            "protocol_error", "external Judge score is below evaluator score_min"
        )
    if maximum is not None and response.score > maximum:
        raise ExternalProtocolError(
            "protocol_error", "external Judge score is above evaluator score_max"
        )

    passed = _passed(context, response.score, response.passed)
    explanation = response.explanation
    if passed is None:
        status = ScoreStatus.MISSING
        explanation = (
            f"{explanation} (Judge did not return passed and no evaluator threshold is configured)"
        )
    else:
        status = ScoreStatus.PASSED if passed else ScoreStatus.FAILED

    return [
        EvaluatorOutcome(
            metric_name=context.evaluator.name,
            status=status,
            value=response.score,
            label=response.label,
            passed=passed,
            explanation=explanation,
            evidence=list(response.evidence),
            provenance=provenance,
            raw_response=result.raw_response,
            raw_result={
                "protocol": "signed_http_json_v1",
                "attempts": result.attempts,
                "response": response.model_copy(
                    update={"provenance": provenance}
                ).model_dump(mode="json"),
                "raw_response": result.raw_response,
            },
        )
    ]
