"""Real model-controlled, side-effect-free Tool Agent for live acceptance."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from agent_eval import DatasetCase, TaskResult
from openai import OpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from .provider_preflight import LiveProviderConfig, _endpoint_origin, _safe_error

ORDER_ID_PATTERN = re.compile(r"^ORDER-[0-9]{4}$")
ORDERS: dict[str, dict[str, Any]] = {
    "ORDER-1001": {"status": "processing", "paid": True, "delivered_days_ago": None},
    "ORDER-1002": {"status": "shipped", "paid": True, "delivered_days_ago": None},
    "ORDER-1003": {"status": "delivered", "paid": True, "delivered_days_ago": 5},
}

SYSTEM_PROMPT = """You are a read-only order support agent used for release evaluation.
You must call exactly one of the supplied tools before answering every request.
Choose lookup_order_status for status questions, check_cancellation_eligibility for cancellation
requests, and check_refund_eligibility for refund requests. Never claim that an order was changed.
Base the final answer only on the returned tool result and mention the order id."""


class ToolAgentProtocolError(RuntimeError):
    """The model or tool call violated the live Tool Agent contract."""


@dataclass(frozen=True)
class ToolExecution:
    name: str
    call_id: str
    arguments: dict[str, str]
    result: dict[str, Any]


def _order_id(arguments: dict[str, Any]) -> str:
    if set(arguments) != {"order_id"}:
        raise ToolAgentProtocolError("tool arguments must contain only order_id")
    order_id = arguments["order_id"]
    if not isinstance(order_id, str) or not ORDER_ID_PATTERN.fullmatch(order_id):
        raise ToolAgentProtocolError("tool order_id must match ORDER-0000")
    return order_id


def lookup_order_status(arguments: dict[str, Any]) -> dict[str, Any]:
    order_id = _order_id(arguments)
    order = ORDERS.get(order_id)
    if order is None:
        return {"order_id": order_id, "found": False, "status": "not_found"}
    return {"order_id": order_id, "found": True, "status": order["status"]}


def check_cancellation_eligibility(arguments: dict[str, Any]) -> dict[str, Any]:
    order_id = _order_id(arguments)
    order = ORDERS.get(order_id)
    if order is None:
        return {
            "order_id": order_id,
            "found": False,
            "status": "not_found",
            "eligible": False,
            "reason": "order_not_found",
        }
    eligible = order["status"] == "processing"
    return {
        "order_id": order_id,
        "found": True,
        "status": order["status"],
        "eligible": eligible,
        "reason": "processing_order" if eligible else "already_in_fulfillment",
    }


def check_refund_eligibility(arguments: dict[str, Any]) -> dict[str, Any]:
    order_id = _order_id(arguments)
    order = ORDERS.get(order_id)
    if order is None:
        return {
            "order_id": order_id,
            "found": False,
            "status": "not_found",
            "eligible": False,
            "reason": "order_not_found",
        }
    delivered_days = order["delivered_days_ago"]
    eligible = (
        order["status"] == "delivered"
        and isinstance(delivered_days, int)
        and delivered_days <= 30
    )
    return {
        "order_id": order_id,
        "found": True,
        "status": order["status"],
        "eligible": eligible,
        "days_since_delivery": delivered_days,
        "reason": "within_refund_window" if eligible else "not_refund_eligible",
    }


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "lookup_order_status": lookup_order_status,
    "check_cancellation_eligibility": check_cancellation_eligibility,
    "check_refund_eligibility": check_refund_eligibility,
}

TOOL_SCHEMAS = cast(list[ChatCompletionToolParam], [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "pattern": "^ORDER-[0-9]{4}$",
                        "description": "The exact fictional order id from the user request.",
                    }
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }
    for name, description in (
        ("lookup_order_status", "Read the current status of an order."),
        (
            "check_cancellation_eligibility",
            "Check whether an order may be cancelled without changing it.",
        ),
        (
            "check_refund_eligibility",
            "Check whether an order is inside the refund window without changing it.",
        ),
    )
])


def _case_request(case: DatasetCase) -> str:
    if not isinstance(case.input, dict):
        raise ToolAgentProtocolError("Dataset Case input must be an object")
    request = case.input.get("request")
    order_id = case.input.get("order_id")
    if not isinstance(request, str) or not request.strip():
        raise ToolAgentProtocolError("Dataset Case input.request must be a non-empty string")
    if not isinstance(order_id, str) or not ORDER_ID_PATTERN.fullmatch(order_id):
        raise ToolAgentProtocolError("Dataset Case input.order_id must match ORDER-0000")
    return f"Order id: {order_id}\nCustomer request: {request.strip()}"


def _tool_calls(message: Any) -> list[Any]:
    calls = getattr(message, "tool_calls", None)
    return list(calls) if calls else []


def _assistant_message(message: Any, calls: list[Any]) -> ChatCompletionAssistantMessageParam:
    serialized_calls = []
    for call in calls:
        function = getattr(call, "function", None)
        serialized_calls.append(
            {
                "id": str(getattr(call, "id", "")),
                "type": "function",
                "function": {
                    "name": str(getattr(function, "name", "")),
                    "arguments": str(getattr(function, "arguments", "")),
                },
            }
        )
    return cast(ChatCompletionAssistantMessageParam, {
        "role": "assistant",
        "content": getattr(message, "content", None),
        "tool_calls": serialized_calls,
    })


class LiveToolAgent:
    def __init__(
        self,
        config: LiveProviderConfig,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        max_turns: int = 4,
    ) -> None:
        if max_turns < 2:
            raise ValueError("max_turns must allow a tool request and final answer")
        self.config = config
        self.system_prompt = system_prompt.strip()
        self.max_turns = max_turns
        self.tracer = trace.get_tracer("agent-eval-live-tool-agent", "0.1.0")

    def run(self, case: DatasetCase) -> TaskResult:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": _case_request(case)},
        ]
        client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
            max_retries=0,
        )
        executions: list[ToolExecution] = []
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        response_models: set[str] = set()
        try:
            for turn in range(1, self.max_turns + 1):
                response = self._model_turn(client, messages, turn, usage, response_models)
                choice = response.choices[0]
                calls = _tool_calls(choice.message)
                if calls:
                    if executions:
                        raise ToolAgentProtocolError(
                            "model requested additional tools after the required tool executed"
                        )
                    if len(calls) != 1:
                        raise ToolAgentProtocolError("model must select exactly one tool")
                    messages.append(_assistant_message(choice.message, calls))
                    execution = self._execute_tool(calls[0], len(executions))
                    executions.append(execution)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": execution.call_id,
                            "content": json.dumps(
                                execution.result,
                                ensure_ascii=True,
                                sort_keys=True,
                            ),
                        }
                    )
                    continue

                content = getattr(choice.message, "content", None)
                if not executions:
                    raise ToolAgentProtocolError("model answered without selecting a tool")
                if not isinstance(content, str) or not content.strip():
                    raise ToolAgentProtocolError("model returned an empty final answer")
                state = executions[-1].result
                return TaskResult(
                    output={"answer": content.strip(), **state},
                    usage=usage,
                    metadata={
                        "execution_origin": "real_llm",
                        "provider_origin": _endpoint_origin(self.config.base_url),
                        "configured_model": self.config.model,
                        "response_models": sorted(response_models),
                    },
                )
        except ToolAgentProtocolError:
            raise
        except Exception as exc:
            raise ToolAgentProtocolError(
                "real Tool Agent provider call failed: "
                + _safe_error(exc, self.config.api_key)
            ) from exc
        finally:
            client.close()
        raise ToolAgentProtocolError("model did not produce a final answer within max_turns")

    def _model_turn(
        self,
        client: OpenAI,
        messages: list[ChatCompletionMessageParam],
        turn: int,
        total_usage: dict[str, int],
        response_models: set[str],
    ) -> Any:
        with self.tracer.start_as_current_span(f"chat.completions.turn-{turn}") as span:
            span.set_attribute("openinference.span.kind", "LLM")
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.request.model", self.config.model)
            span.set_attribute("agent_eval.turn.index", turn)
            span.set_attribute("agent_eval.input", json.dumps(messages, ensure_ascii=True))
            try:
                response = client.chat.completions.create(
                    model=self.config.model,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    tool_choice="auto",
                )
                if not response.choices:
                    raise ToolAgentProtocolError("upstream response contains no choices")
                response_model = str(response.model or "").strip()
                request_id = str(response.id or "").strip()
                if not response_model or not request_id:
                    raise ToolAgentProtocolError(
                        "upstream response is missing request id or model identity"
                    )
                if self.config.api_key in response_model or self.config.api_key in request_id:
                    raise ToolAgentProtocolError(
                        "upstream response placed credential material in public metadata"
                    )
                if response.usage is None:
                    raise ToolAgentProtocolError("upstream response is missing usage metadata")
                turn_usage = {
                    "input_tokens": self._token_count(response.usage, "prompt_tokens"),
                    "output_tokens": self._token_count(response.usage, "completion_tokens"),
                    "total_tokens": self._token_count(response.usage, "total_tokens"),
                }
                if turn_usage["total_tokens"] <= 0:
                    raise ToolAgentProtocolError("upstream usage total must be greater than zero")
                if turn_usage["total_tokens"] < (
                    turn_usage["input_tokens"] + turn_usage["output_tokens"]
                ):
                    raise ToolAgentProtocolError("upstream usage totals are inconsistent")
                for key, value in turn_usage.items():
                    total_usage[key] += value
                response_models.add(response_model)
                span.set_attribute("gen_ai.response.model", response_model)
                span.set_attribute("gen_ai.response.id", request_id)
                span.set_attribute(
                    "gen_ai.response.finish_reasons",
                    [response.choices[0].finish_reason],
                )
                span.set_attribute("gen_ai.usage.input_tokens", turn_usage["input_tokens"])
                span.set_attribute("gen_ai.usage.output_tokens", turn_usage["output_tokens"])
                span.set_attribute("gen_ai.usage.total_tokens", turn_usage["total_tokens"])
                span.set_attribute("agent_eval.usage.present", True)
                span.set_attribute("agent_eval.usage", json.dumps(turn_usage, sort_keys=True))
                span.set_attribute(
                    "agent_eval.output",
                    json.dumps(
                        _assistant_message(
                            response.choices[0].message,
                            _tool_calls(response.choices[0].message),
                        ),
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                )
                return response
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise

    @staticmethod
    def _token_count(usage: Any, name: str) -> int:
        value = getattr(usage, name, None)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ToolAgentProtocolError(f"upstream response is missing valid usage.{name}")
        return value

    def _execute_tool(self, call: Any, order: int) -> ToolExecution:
        if str(getattr(call, "type", "function")) != "function":
            raise ToolAgentProtocolError("provider returned a non-function tool call")
        call_id = str(getattr(call, "id", "")).strip()
        function = getattr(call, "function", None)
        name = str(getattr(function, "name", "")).strip()
        raw_arguments = getattr(function, "arguments", "")
        if not call_id or name not in TOOL_HANDLERS:
            raise ToolAgentProtocolError("provider returned an unknown or unidentifiable tool call")
        try:
            arguments = json.loads(raw_arguments)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ToolAgentProtocolError("provider returned malformed tool arguments") from exc
        if not isinstance(arguments, dict):
            raise ToolAgentProtocolError("provider tool arguments must be a JSON object")

        with self.tracer.start_as_current_span(name) as tool_span:
            tool_span.set_attribute("openinference.span.kind", "TOOL")
            tool_span.set_attribute("tool.name", name)
            tool_span.set_attribute("tool.call.id", call_id)
            tool_span.set_attribute("tool.order", order)
            tool_span.set_attribute(
                "agent_eval.input", json.dumps(arguments, ensure_ascii=True, sort_keys=True)
            )
            result = TOOL_HANDLERS[name](arguments)
            with self.tracer.start_as_current_span(f"{name}.result") as result_span:
                result_span.set_attribute("openinference.span.kind", "TOOL_RESULT")
                result_span.set_attribute("tool.name", name)
                result_span.set_attribute("tool.call.id", call_id)
                result_span.set_attribute(
                    "agent_eval.output", json.dumps(result, ensure_ascii=True, sort_keys=True)
                )
            tool_span.set_attribute(
                "agent_eval.output", json.dumps(result, ensure_ascii=True, sort_keys=True)
            )
        return ToolExecution(
            name=name,
            call_id=call_id,
            arguments={key: str(value) for key, value in arguments.items()},
            result=result,
        )
