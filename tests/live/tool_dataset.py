"""Versioned business cases and deterministic evaluators for live Tool acceptance."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx

DATASET_NAME = "Live Tool Agent acceptance"
DATASET_DESCRIPTION = (
    "Controlled, side-effect-free order support cases for real model Tool Agent acceptance."
)
DATASET_SCHEMA_VERSION = "1.1.0"

TOOL_CASES: list[dict[str, Any]] = [
    # ---- Status lookups -------------------------------------------------
    {
        "id": "order-status-processing",
        "input": {
            "order_id": "ORDER-1001",
            "request": "What is the current status of this order?",
        },
        "expected_tools": [
            {
                "name": "lookup_order_status",
                "arguments": {"order_id": "ORDER-1001"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "processing"},
        "criteria": [
            "Use the read-only order status tool for a status request.",
            "Report the state returned by the tool without changing the order.",
        ],
        "metadata": {"business_rule": "status_lookup", "tool_required": True},
    },
    {
        "id": "order-status-shipped",
        "input": {
            "order_id": "ORDER-1002",
            "request": "Where is my order right now?",
        },
        "expected_tools": [
            {
                "name": "lookup_order_status",
                "arguments": {"order_id": "ORDER-1002"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "shipped"},
        "criteria": [
            "Use the read-only order status tool for a status request.",
            "Report the state returned by the tool without changing the order.",
        ],
        "metadata": {"business_rule": "status_lookup", "tool_required": True},
    },
    {
        "id": "order-status-delivered",
        "input": {
            "order_id": "ORDER-1003",
            "request": "Has my order been delivered?",
        },
        "expected_tools": [
            {
                "name": "lookup_order_status",
                "arguments": {"order_id": "ORDER-1003"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "delivered"},
        "criteria": [
            "Use the read-only order status tool for a status request.",
            "Report the state returned by the tool without changing the order.",
        ],
        "metadata": {"business_rule": "status_lookup", "tool_required": True},
    },
    {
        "id": "order-status-unpaid-processing",
        "input": {
            "order_id": "ORDER-1007",
            "request": "Can you tell me the status of this order?",
        },
        "expected_tools": [
            {
                "name": "lookup_order_status",
                "arguments": {"order_id": "ORDER-1007"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "processing"},
        "criteria": [
            "Use the read-only order status tool for a status request.",
            "Report the state returned by the tool without changing the order.",
        ],
        "metadata": {"business_rule": "status_lookup", "tool_required": True},
    },
    {
        "id": "status-unknown-order",
        "input": {
            "order_id": "ORDER-9999",
            "request": "What is the current status of this order?",
        },
        "expected_tools": [
            {
                "name": "lookup_order_status",
                "arguments": {"order_id": "ORDER-9999"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "not_found", "found": False},
        "criteria": [
            "Use the read-only status tool even when the order may not exist.",
            "Report that the order was not found without inventing a status.",
        ],
        "metadata": {"business_rule": "unknown_order", "tool_required": True},
    },
    {
        "id": "status-unknown-order-cancel",
        "input": {
            "order_id": "ORDER-8888",
            "request": "Can I cancel this order?",
        },
        "expected_tools": [
            {
                "name": "check_cancellation_eligibility",
                "arguments": {"order_id": "ORDER-8888"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "not_found", "found": False, "eligible": False},
        "criteria": [
            "Use the cancellation-eligibility tool even when the order may not exist.",
            "Report that the order was not found without claiming a cancellation.",
        ],
        "metadata": {"business_rule": "unknown_order_cancel", "tool_required": True},
    },
    # ---- Cancellation eligibility --------------------------------------
    {
        "id": "cancel-processing-order",
        "input": {
            "order_id": "ORDER-1001",
            "request": "Please cancel this order before it is shipped.",
        },
        "expected_tools": [
            {
                "name": "check_cancellation_eligibility",
                "arguments": {"order_id": "ORDER-1001"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "processing", "eligible": True},
        "criteria": [
            "Check cancellation eligibility before claiming that an order can be cancelled.",
            "A processing order is eligible under the controlled business rules.",
        ],
        "metadata": {
            "business_rule": "cancellation_eligibility",
            "tool_required": True,
        },
    },
    {
        "id": "cancel-processing-order-1004",
        "input": {
            "order_id": "ORDER-1004",
            "request": "I changed my mind, cancel this order.",
        },
        "expected_tools": [
            {
                "name": "check_cancellation_eligibility",
                "arguments": {"order_id": "ORDER-1004"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "processing", "eligible": True},
        "criteria": [
            "Check cancellation eligibility before claiming that an order can be cancelled.",
            "A processing order is eligible under the controlled business rules.",
        ],
        "metadata": {
            "business_rule": "cancellation_eligibility",
            "tool_required": True,
        },
    },
    {
        "id": "cancel-unpaid-processing-order",
        "input": {
            "order_id": "ORDER-1007",
            "request": "I haven't paid yet, cancel it.",
        },
        "expected_tools": [
            {
                "name": "check_cancellation_eligibility",
                "arguments": {"order_id": "ORDER-1007"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "processing", "eligible": True},
        "criteria": [
            "Check cancellation eligibility before claiming that an order can be cancelled.",
            "A processing order is eligible regardless of payment state under the rules.",
        ],
        "metadata": {
            "business_rule": "cancellation_eligibility",
            "tool_required": True,
        },
    },
    {
        "id": "cancel-shipped-order",
        "input": {
            "order_id": "ORDER-1002",
            "request": "Can I cancel this order?",
        },
        "expected_tools": [
            {
                "name": "check_cancellation_eligibility",
                "arguments": {"order_id": "ORDER-1002"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "shipped", "eligible": False},
        "criteria": [
            "Check cancellation eligibility instead of changing the order.",
            "A shipped order is not cancellable under the controlled business rules.",
        ],
        "metadata": {
            "business_rule": "cancellation_eligibility",
            "tool_required": True,
        },
    },
    {
        "id": "cancel-shipped-order-1005",
        "input": {
            "order_id": "ORDER-1005",
            "request": "Stop the shipment and cancel this order.",
        },
        "expected_tools": [
            {
                "name": "check_cancellation_eligibility",
                "arguments": {"order_id": "ORDER-1005"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "shipped", "eligible": False},
        "criteria": [
            "Check cancellation eligibility instead of changing the order.",
            "A shipped order is not cancellable under the controlled business rules.",
        ],
        "metadata": {
            "business_rule": "cancellation_eligibility",
            "tool_required": True,
        },
    },
    {
        "id": "cancel-delivered-order",
        "input": {
            "order_id": "ORDER-1003",
            "request": "Cancel this order even though it already arrived.",
        },
        "expected_tools": [
            {
                "name": "check_cancellation_eligibility",
                "arguments": {"order_id": "ORDER-1003"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "delivered", "eligible": False},
        "criteria": [
            "Check cancellation eligibility instead of changing the order.",
            "A delivered order is not cancellable under the controlled business rules.",
        ],
        "metadata": {
            "business_rule": "cancellation_eligibility",
            "tool_required": True,
        },
    },
    # ---- Refund eligibility ---------------------------------------------
    {
        "id": "refund-recent-delivery",
        "input": {
            "order_id": "ORDER-1003",
            "request": "Is this delivered order still eligible for a refund?",
        },
        "expected_tools": [
            {
                "name": "check_refund_eligibility",
                "arguments": {"order_id": "ORDER-1003"},
                "order": 0,
            }
        ],
        "expected_state": {
            "status": "delivered",
            "eligible": True,
            "days_since_delivery": 5,
        },
        "criteria": [
            "Check refund eligibility instead of issuing a refund.",
            "A delivery from 5 days ago is inside the controlled 30-day refund window.",
        ],
        "metadata": {"business_rule": "refund_eligibility", "tool_required": True},
    },
    {
        "id": "refund-12-days",
        "input": {
            "order_id": "ORDER-1006",
            "request": "Can I get a refund on this delivered order?",
        },
        "expected_tools": [
            {
                "name": "check_refund_eligibility",
                "arguments": {"order_id": "ORDER-1006"},
                "order": 0,
            }
        ],
        "expected_state": {
            "status": "delivered",
            "eligible": True,
            "days_since_delivery": 12,
        },
        "criteria": [
            "Check refund eligibility instead of issuing a refund.",
            "A delivery from 12 days ago is inside the controlled 30-day refund window.",
        ],
        "metadata": {"business_rule": "refund_eligibility", "tool_required": True},
    },
    {
        "id": "refund-29-days-boundary",
        "input": {
            "order_id": "ORDER-1009",
            "request": "I want a refund, it was delivered 29 days ago.",
        },
        "expected_tools": [
            {
                "name": "check_refund_eligibility",
                "arguments": {"order_id": "ORDER-1009"},
                "order": 0,
            }
        ],
        "expected_state": {
            "status": "delivered",
            "eligible": True,
            "days_since_delivery": 29,
        },
        "criteria": [
            "Check refund eligibility instead of issuing a refund.",
            "A delivery from 29 days ago is still inside the 30-day window (boundary).",
        ],
        "metadata": {"business_rule": "refund_eligibility_boundary", "tool_required": True},
    },
    {
        "id": "refund-31-days-outside-window",
        "input": {
            "order_id": "ORDER-1010",
            "request": "I want a refund, it was delivered 31 days ago.",
        },
        "expected_tools": [
            {
                "name": "check_refund_eligibility",
                "arguments": {"order_id": "ORDER-1010"},
                "order": 0,
            }
        ],
        "expected_state": {
            "status": "delivered",
            "eligible": False,
            "days_since_delivery": 31,
        },
        "criteria": [
            "Check refund eligibility instead of issuing a refund.",
            "A delivery from 31 days ago is outside the 30-day window (boundary).",
        ],
        "metadata": {"business_rule": "refund_eligibility_boundary", "tool_required": True},
    },
    {
        "id": "refund-90-days-far-out",
        "input": {
            "order_id": "ORDER-1011",
            "request": "This was delivered three months ago, refund it.",
        },
        "expected_tools": [
            {
                "name": "check_refund_eligibility",
                "arguments": {"order_id": "ORDER-1011"},
                "order": 0,
            }
        ],
        "expected_state": {
            "status": "delivered",
            "eligible": False,
            "days_since_delivery": 90,
        },
        "criteria": [
            "Check refund eligibility instead of issuing a refund.",
            "A delivery from 90 days ago is far outside the 30-day window.",
        ],
        "metadata": {"business_rule": "refund_eligibility", "tool_required": True},
    },
    {
        "id": "refund-shipped-not-delivered",
        "input": {
            "order_id": "ORDER-1002",
            "request": "I want my money back for this order.",
        },
        "expected_tools": [
            {
                "name": "check_refund_eligibility",
                "arguments": {"order_id": "ORDER-1002"},
                "order": 0,
            }
        ],
        "expected_state": {
            "status": "shipped",
            "eligible": False,
            "days_since_delivery": None,
        },
        "criteria": [
            "Check refund eligibility instead of issuing a refund.",
            "A shipped (not delivered) order is not refundable under the rules.",
        ],
        "metadata": {"business_rule": "refund_eligibility", "tool_required": True},
    },
    {
        "id": "refund-processing-order",
        "input": {
            "order_id": "ORDER-1004",
            "request": "Refund this order that hasn't shipped yet.",
        },
        "expected_tools": [
            {
                "name": "check_refund_eligibility",
                "arguments": {"order_id": "ORDER-1004"},
                "order": 0,
            }
        ],
        "expected_state": {
            "status": "processing",
            "eligible": False,
            "days_since_delivery": None,
        },
        "criteria": [
            "Check refund eligibility instead of issuing a refund.",
            "A processing order is not refundable (not delivered) under the rules.",
        ],
        "metadata": {"business_rule": "refund_eligibility", "tool_required": True},
    },
    # ---- Cross-scenario: status query on unknown order ids -----------------
    {
        "id": "status-unknown-order-8888",
        "input": {
            "order_id": "ORDER-8888",
            "request": "Where is this order?",
        },
        "expected_tools": [
            {
                "name": "lookup_order_status",
                "arguments": {"order_id": "ORDER-8888"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "not_found", "found": False},
        "criteria": [
            "Use the read-only status tool even when the order may not exist.",
            "Report that the order was not found without inventing a status.",
        ],
        "metadata": {"business_rule": "unknown_order", "tool_required": True},
    },
    {
        "id": "status-delivered-1006",
        "input": {
            "order_id": "ORDER-1006",
            "request": "What is the delivery status?",
        },
        "expected_tools": [
            {
                "name": "lookup_order_status",
                "arguments": {"order_id": "ORDER-1006"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "delivered"},
        "criteria": [
            "Use the read-only order status tool for a status request.",
            "Report the state returned by the tool without changing the order.",
        ],
        "metadata": {"business_rule": "status_lookup", "tool_required": True},
    },
    {
        "id": "status-shipped-1008",
        "input": {
            "order_id": "ORDER-1008",
            "request": "Is this order on its way?",
        },
        "expected_tools": [
            {
                "name": "lookup_order_status",
                "arguments": {"order_id": "ORDER-1008"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "shipped"},
        "criteria": [
            "Use the read-only order status tool for a status request.",
            "Report the state returned by the tool without changing the order.",
        ],
        "metadata": {"business_rule": "status_lookup", "tool_required": True},
    },
    {
        "id": "status-delivered-1011",
        "input": {
            "order_id": "ORDER-1011",
            "request": "When was this order delivered?",
        },
        "expected_tools": [
            {
                "name": "lookup_order_status",
                "arguments": {"order_id": "ORDER-1011"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "delivered"},
        "criteria": [
            "Use the read-only order status tool for a status request.",
            "Report the state returned by the tool without changing the order.",
        ],
        "metadata": {"business_rule": "status_lookup", "tool_required": True},
    },
    {
        "id": "cancel-shipped-order-1008",
        "input": {
            "order_id": "ORDER-1008",
            "request": "Cancel this unpaid shipped order.",
        },
        "expected_tools": [
            {
                "name": "check_cancellation_eligibility",
                "arguments": {"order_id": "ORDER-1008"},
                "order": 0,
            }
        ],
        "expected_state": {"status": "shipped", "eligible": False},
        "criteria": [
            "Check cancellation eligibility instead of changing the order.",
            "A shipped order is not cancellable regardless of payment state.",
        ],
        "metadata": {
            "business_rule": "cancellation_eligibility",
            "tool_required": True,
        },
    },
    {
        "id": "refund-delivered-1009",
        "input": {
            "order_id": "ORDER-1009",
            "request": "Can I still get a refund on this one?",
        },
        "expected_tools": [
            {
                "name": "check_refund_eligibility",
                "arguments": {"order_id": "ORDER-1009"},
                "order": 0,
            }
        ],
        "expected_state": {
            "status": "delivered",
            "eligible": True,
            "days_since_delivery": 29,
        },
        "criteria": [
            "Check refund eligibility instead of issuing a refund.",
            "A delivery from 29 days ago is inside the 30-day window (boundary).",
        ],
        "metadata": {"business_rule": "refund_eligibility_boundary", "tool_required": True},
    },
]

EVALUATOR_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "live_tool_selection",
        "version": DATASET_SCHEMA_VERSION,
        "evaluator_type": "deterministic",
        "requires": ["expected_tools", "tool_calls"],
        "supported_agent_types": ["tool"],
        "score_min": 0,
        "score_max": 1,
        "direction": "higher_is_better",
        "default_threshold": 1,
        "config": {"metric": "tool_correctness", "ordered": True},
    },
    {
        "name": "live_tool_arguments",
        "version": DATASET_SCHEMA_VERSION,
        "evaluator_type": "deterministic",
        "requires": ["expected_tools", "tool_calls"],
        "supported_agent_types": ["tool"],
        "score_min": 0,
        "score_max": 1,
        "direction": "higher_is_better",
        "default_threshold": 1,
        "config": {"metric": "argument_correctness"},
    },
    {
        "name": "live_business_state",
        "version": DATASET_SCHEMA_VERSION,
        "evaluator_type": "deterministic",
        "requires": ["expected_state"],
        "supported_agent_types": ["tool"],
        "score_min": 0,
        "score_max": 1,
        "direction": "higher_is_better",
        "default_threshold": 1,
        "config": {"metric": "task_success"},
    },
]


class ToolDatasetSetupError(RuntimeError):
    """Raised when live acceptance resources cannot be prepared safely."""


@dataclass(frozen=True)
class ToolAcceptanceResources:
    dataset_id: str
    dataset_version_id: str
    evaluator_version_ids: tuple[str, ...]
    manifest_sha256: str


def manifest_sha256(cases: list[dict[str, Any]] | None = None) -> str:
    selected = TOOL_CASES if cases is None else cases
    payload = json.dumps(
        sorted(selected, key=lambda case: str(case.get("id", ""))),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_tool_cases(cases: list[dict[str, Any]] = TOOL_CASES) -> None:
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ToolDatasetSetupError("Tool Dataset Case ids must be unique and non-empty")
        seen.add(case_id)
        expected_tools = case.get("expected_tools")
        expected_state = case.get("expected_state")
        criteria = case.get("criteria")
        if not isinstance(expected_tools, list) or len(expected_tools) != 1:
            raise ToolDatasetSetupError(f"Case {case_id} must declare exactly one expected tool")
        tool = expected_tools[0]
        if not isinstance(tool, dict) or tool.get("order") != 0:
            raise ToolDatasetSetupError(f"Case {case_id} must explain the first tool call")
        arguments = tool.get("arguments")
        if not isinstance(arguments, dict) or arguments.get("order_id") != case["input"].get(
            "order_id"
        ):
            raise ToolDatasetSetupError(f"Case {case_id} expected tool must target its order")
        if not isinstance(expected_state, dict) or not expected_state:
            raise ToolDatasetSetupError(f"Case {case_id} must declare expected business state")
        if not isinstance(criteria, list) or not criteria or not all(criteria):
            raise ToolDatasetSetupError(f"Case {case_id} must explain its business assertions")


def _request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> Any:
    try:
        response = client.request(method, path, json=json_body)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ToolDatasetSetupError(f"Agent Eval API is unreachable for {method} {path}") from exc
    if response.is_error:
        raise ToolDatasetSetupError(
            f"Agent Eval API rejected {method} {path} with HTTP {response.status_code}"
        )
    return response.json()


def _dataset_metadata() -> dict[str, Any]:
    return {
        "acceptance_kind": "real_tool_agent",
        "schema_version": DATASET_SCHEMA_VERSION,
        "manifest_sha256": manifest_sha256(),
    }


def _declared_case_fields(case: dict[str, Any]) -> dict[str, Any]:
    return {
        key: case.get(key)
        for key in (
            "id",
            "input",
            "criteria",
            "expected_tools",
            "expected_state",
            "metadata",
        )
    }


def _find_or_create_dataset(client: httpx.Client, project_id: str) -> tuple[str, str]:
    datasets = _request_json(client, "GET", f"/projects/{project_id}/datasets")
    matches = [item for item in datasets if item.get("name") == DATASET_NAME]
    if len(matches) > 1:
        raise ToolDatasetSetupError(f"multiple Datasets are named {DATASET_NAME!r}")
    metadata = _dataset_metadata()
    if not matches:
        created = _request_json(
            client,
            "POST",
            f"/projects/{project_id}/datasets",
            json_body={
                "name": DATASET_NAME,
                "description": DATASET_DESCRIPTION,
                "tags": ["live-acceptance", "tool-agent"],
                "cases": TOOL_CASES,
                "metadata": metadata,
            },
        )
        return str(created["id"]), str(created["current_version_id"])

    dataset_id = str(matches[0]["id"])
    versions = _request_json(
        client, "GET", f"/projects/{project_id}/datasets/{dataset_id}/versions"
    )
    matching = [
        version
        for version in versions
        if version.get("metadata", {}).get("manifest_sha256") == metadata["manifest_sha256"]
        and sorted(
            (_declared_case_fields(case) for case in version.get("cases", [])),
            key=lambda case: str(case["id"]),
        )
        == sorted(
            (_declared_case_fields(case) for case in TOOL_CASES),
            key=lambda case: str(case["id"]),
        )
    ]
    if matching:
        selected = max(matching, key=lambda item: int(item["version"]))
        return dataset_id, str(selected["id"])
    created_version = _request_json(
        client,
        "POST",
        f"/projects/{project_id}/datasets/{dataset_id}/versions",
        json_body={"cases": TOOL_CASES, "metadata": metadata},
    )
    return dataset_id, str(created_version["id"])


def _evaluator_identity(payload: dict[str, Any]) -> dict[str, Any]:
    fields = EVALUATOR_DEFINITIONS[0].keys()
    return {key: payload.get(key) for key in fields}


def _find_or_create_evaluators(client: httpx.Client, project_id: str) -> tuple[str, ...]:
    existing = _request_json(client, "GET", f"/projects/{project_id}/evaluators")
    ids: list[str] = []
    for definition in EVALUATOR_DEFINITIONS:
        matches = [
            item
            for item in existing
            if item.get("name") == definition["name"]
            and item.get("version") == definition["version"]
        ]
        if len(matches) > 1:
            raise ToolDatasetSetupError(
                f"multiple Evaluators have identity {definition['name']}:{definition['version']}"
            )
        if matches:
            if _evaluator_identity(matches[0]) != _evaluator_identity(definition):
                raise ToolDatasetSetupError(
                    "Evaluator "
                    f"{definition['name']}:{definition['version']} has configuration drift"
                )
            ids.append(str(matches[0]["id"]))
            continue
        created = _request_json(
            client,
            "POST",
            f"/projects/{project_id}/evaluators",
            json_body=definition,
        )
        ids.append(str(created["id"]))
    return tuple(ids)


def ensure_tool_acceptance_resources(
    client: httpx.Client,
    project_id: str,
) -> ToolAcceptanceResources:
    validate_tool_cases()
    dataset_id, version_id = _find_or_create_dataset(client, project_id)
    evaluator_ids = _find_or_create_evaluators(client, project_id)
    return ToolAcceptanceResources(
        dataset_id=dataset_id,
        dataset_version_id=version_id,
        evaluator_version_ids=evaluator_ids,
        manifest_sha256=manifest_sha256(),
    )
