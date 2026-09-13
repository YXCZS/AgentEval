"""Deterministic HTTP Agent fixtures for contract tests only.

These fixtures intentionally live under ``tests`` and are never packaged into
the API, Worker, or Docker images. They validate legacy protocol compatibility;
they are not product runtime Agents.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI

custom_app = FastAPI(title="Custom Agent Test Fixture")


@custom_app.post("/run")
def run_custom(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("input")
    if isinstance(value, dict):
        subject = str(value.get("subject", value.get("text", "unknown")))
        priority = str(value.get("priority", "normal"))
    else:
        subject = str(value or "unknown")
        priority = "normal"
    output = {
        "category": "custom",
        "subject": subject.strip(),
        "priority": priority.lower(),
        "accepted": bool(subject.strip()),
    }
    return {
        "output": output,
        "tool_calls": [],
        "usage": {"input_tokens": max(1, len(subject.split())), "output_tokens": 8, "cost": 0.0},
    }


order_app = FastAPI(title="Order Agent Test Fixture")
_ORDERS: dict[str, dict[str, str]] = {
    "ORD-1001": {"status": "processing", "item": "wireless keyboard"},
    "ORD-1002": {"status": "shipped", "item": "desk lamp"},
    "ORD-1003": {"status": "delivered", "item": "monitor stand"},
}


def _request(payload: dict[str, Any]) -> tuple[str, str, str | None]:
    value = payload.get("input", "")
    if isinstance(value, dict):
        action = str(value.get("action", "query")).lower()
        order_id = value.get("order_id")
        return action, str(value.get("message", "")), str(order_id) if order_id else None
    text = str(value)
    lowered = text.lower()
    action = "refund" if "refund" in lowered else "cancel" if "cancel" in lowered else "query"
    match = re.search(r"\bORD-\d+\b", text, re.IGNORECASE)
    return action, text, match.group(0).upper() if match else None


def _response(output: dict[str, Any], tools: list[dict[str, Any]], text: str) -> dict[str, Any]:
    return {
        "output": output,
        "tool_calls": tools,
        "usage": {"input_tokens": max(1, len(text.split())), "output_tokens": 14, "cost": 0.0},
    }


@order_app.post("/run")
def run_order(payload: dict[str, Any]) -> dict[str, Any]:
    action, text, order_id = _request(payload)
    if order_id is None:
        return _response({"status": "needs_order_id"}, [], text)
    lookup = {"name": "lookup_order", "arguments": {"order_id": order_id}, "order": 0}
    order = _ORDERS.get(order_id)
    if order is None:
        return _response({"status": "not_found", "order_id": order_id}, [lookup], text)
    state = order["status"]
    if action == "cancel":
        if state != "processing":
            return _response({"status": "blocked", "order_id": order_id}, [lookup], text)
        return _response(
            {"status": "cancelled", "order_id": order_id},
            [lookup, {"name": "cancel_order", "arguments": {"order_id": order_id}, "order": 1}],
            text,
        )
    if action == "refund":
        if state != "delivered":
            return _response({"status": "blocked", "order_id": order_id}, [lookup], text)
        return _response(
            {"status": "refund_requested", "order_id": order_id},
            [lookup, {"name": "request_refund", "arguments": {"order_id": order_id}, "order": 1}],
            text,
        )
    return _response({"status": state, "order_id": order_id, "item": order["item"]}, [lookup], text)


rag_app = FastAPI(title="RAG Agent Test Fixture")
_DOCUMENTS = {
    "refund": "Refunds are available for delivered orders within 30 days.",
    "cancel": "Orders can be cancelled only while they are still processing.",
    "shipping": "Orders move from processing to shipped and then delivered.",
}


@rag_app.post("/run")
def run_rag(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("input", "")
    if isinstance(value, dict):
        value = value.get("query", value.get("question", ""))
    query = str(value).strip()
    keyword = next((key for key in _DOCUMENTS if key in query.lower()), "shipping")
    context = _DOCUMENTS[keyword]
    return {
        "output": {"answer": context, "citations": [keyword]},
        "tool_calls": [{"name": "retrieve_policy", "arguments": {"query": query}, "order": 0}],
        "usage": {"input_tokens": max(1, len(query.split())), "output_tokens": 12, "cost": 0.0},
        "trace": {"retrieval_context": [{"document_id": keyword, "content": context}]},
    }
