"""Versioned controlled RAG cases required by the real ingestion acceptance lane."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx

DATASET_NAME = "Live RAG Agent ingestion acceptance"
DATASET_DESCRIPTION = (
    "Controlled support-policy cases for real embedding, retrieval, and answer evidence."
)
DATASET_SCHEMA_VERSION = "1.0.0"

RAG_CASES: list[dict[str, Any]] = [
    {
        "id": "rag-refund-window",
        "input": {"question": "How long after delivery can I request a refund?"},
        "retrieval_context": [
            {
                "document_id": "policy-refund-window",
                "content": "A delivered order can be refunded within 30 calendar days of delivery.",
            }
        ],
        "output_schema": {
            "type": "object",
            "required": ["answer", "citations", "retrieved_document_ids"],
            "additionalProperties": False,
            "properties": {
                "answer": {"type": "string", "minLength": 1},
                "citations": {"type": "array", "items": {"type": "string"}},
                "retrieved_document_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
            },
        },
        "criteria": [
            "Retrieve the controlled refund policy document using a real embedding request.",
            "Return a structured answer that cites only retrieved document IDs.",
        ],
        "metadata": {"retrieval_required": True, "business_rule": "refund_window"},
    },
    {
        "id": "rag-cancellation-policy",
        "input": {"question": "When may an order be cancelled?"},
        "retrieval_context": [
            {
                "document_id": "policy-cancel-processing",
                "content": "An order can be cancelled only while its status is processing.",
            }
        ],
        "output_schema": {
            "type": "object",
            "required": ["answer", "citations", "retrieved_document_ids"],
            "additionalProperties": False,
            "properties": {
                "answer": {"type": "string", "minLength": 1},
                "citations": {"type": "array", "items": {"type": "string"}},
                "retrieved_document_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
            },
        },
        "criteria": [
            "Retrieve the controlled cancellation policy document using a real embedding request.",
            "Return a structured answer that cites only retrieved document IDs.",
        ],
        "metadata": {"retrieval_required": True, "business_rule": "cancel_processing"},
    },
    {
        "id": "rag-tracking-availability",
        "input": {"question": "When is package tracking available for my order?"},
        "retrieval_context": [
            {
                "document_id": "policy-shipping-tracking",
                "content": "Tracking is available after an order is shipped and is sent by email.",
            }
        ],
        "output_schema": {
            "type": "object",
            "required": ["answer", "citations", "retrieved_document_ids"],
            "additionalProperties": False,
            "properties": {
                "answer": {"type": "string", "minLength": 1},
                "citations": {"type": "array", "items": {"type": "string"}},
                "retrieved_document_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
            },
        },
        "criteria": [
            "Retrieve the controlled tracking policy document using a real embedding request.",
            "Return a structured answer that cites only retrieved document IDs.",
        ],
        "metadata": {"retrieval_required": True, "business_rule": "shipping_tracking"},
    },
    {
        "id": "rag-return-address",
        "input": {"question": "Where should I ship a product return?"},
        "retrieval_context": [
            {
                "document_id": "policy-return-address",
                "content": "Returns must be sent to the warehouse at 12 Fulfillment Way.",
            }
        ],
        "output_schema": {
            "type": "object",
            "required": ["answer", "citations", "retrieved_document_ids"],
            "additionalProperties": False,
            "properties": {
                "answer": {"type": "string", "minLength": 1},
                "citations": {"type": "array", "items": {"type": "string"}},
                "retrieved_document_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
            },
        },
        "criteria": [
            "Retrieve the controlled return-address policy document using a real embedding request.",
            "Return a structured answer that cites only retrieved document IDs.",
        ],
        "metadata": {"retrieval_required": True, "business_rule": "return_address"},
    },
    {
        "id": "rag-account-deletion",
        "input": {"question": "When can I delete my account?"},
        "retrieval_context": [
            {
                "document_id": "policy-account-deletion",
                "content": "An account can be deleted only after all orders are delivered.",
            }
        ],
        "output_schema": {
            "type": "object",
            "required": ["answer", "citations", "retrieved_document_ids"],
            "additionalProperties": False,
            "properties": {
                "answer": {"type": "string", "minLength": 1},
                "citations": {"type": "array", "items": {"type": "string"}},
                "retrieved_document_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
            },
        },
        "criteria": [
            "Retrieve the controlled account-deletion policy document using a real embedding request.",
            "Return a structured answer that cites only retrieved document IDs.",
        ],
        "metadata": {"retrieval_required": True, "business_rule": "account_deletion"},
    },
    {
        "id": "rag-price-adjustment",
        "input": {"question": "Am I eligible for a price adjustment?"},
        "retrieval_context": [
            {
                "document_id": "policy-price-adjustment",
                "content": "A price adjustment is granted only when the item price drops within 7 days.",
            }
        ],
        "output_schema": {
            "type": "object",
            "required": ["answer", "citations", "retrieved_document_ids"],
            "additionalProperties": False,
            "properties": {
                "answer": {"type": "string", "minLength": 1},
                "citations": {"type": "array", "items": {"type": "string"}},
                "retrieved_document_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
            },
        },
        "criteria": [
            "Retrieve the controlled price-adjustment policy document using a real embedding request.",
            "Return a structured answer that cites only retrieved document IDs.",
        ],
        "metadata": {"retrieval_required": True, "business_rule": "price_adjustment"},
    },
]

EVALUATOR_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "live_rag_retrieval_recall",
        "version": DATASET_SCHEMA_VERSION,
        "evaluator_type": "deterministic",
        "requires": ["retrieval_context", "trace"],
        "supported_agent_types": ["rag"],
        "score_min": 0,
        "score_max": 1,
        "direction": "higher_is_better",
        "default_threshold": 1,
        "config": {"metric": "context_recall"},
    },
    {
        "name": "live_rag_output_schema",
        "version": DATASET_SCHEMA_VERSION,
        "evaluator_type": "deterministic",
        "requires": ["output_schema"],
        "supported_agent_types": ["rag"],
        "score_min": 0,
        "score_max": 1,
        "direction": "higher_is_better",
        "default_threshold": 1,
        "config": {"metric": "json_schema"},
    },
]


class RagDatasetSetupError(RuntimeError):
    """Raised when controlled RAG acceptance resources cannot be prepared safely."""


@dataclass(frozen=True)
class RagAcceptanceResources:
    dataset_id: str
    dataset_version_id: str
    evaluator_version_ids: tuple[str, ...]
    manifest_sha256: str


def manifest_sha256(cases: list[dict[str, Any]] | None = None) -> str:
    payload = json.dumps(
        sorted(RAG_CASES if cases is None else cases, key=lambda case: str(case["id"])),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_rag_cases(cases: list[dict[str, Any]] = RAG_CASES) -> None:
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise RagDatasetSetupError("RAG Dataset Case IDs must be unique and non-empty")
        seen.add(case_id)
        input_data = case.get("input")
        question = input_data.get("question") if isinstance(input_data, dict) else None
        contexts = case.get("retrieval_context")
        schema = case.get("output_schema")
        criteria = case.get("criteria")
        if not isinstance(question, str) or not question.strip():
            raise RagDatasetSetupError(f"Case {case_id} must declare an input question")
        if not isinstance(contexts, list) or len(contexts) != 1:
            raise RagDatasetSetupError(
                f"Case {case_id} must declare one expected reference document"
            )
        document_id = contexts[0].get("document_id") if isinstance(contexts[0], dict) else None
        if not isinstance(document_id, str) or not document_id.startswith("policy-"):
            raise RagDatasetSetupError(
                f"Case {case_id} must declare an explainable policy document"
            )
        if not isinstance(schema, dict) or set(schema.get("required", [])) != {
            "answer",
            "citations",
            "retrieved_document_ids",
        }:
            raise RagDatasetSetupError(f"Case {case_id} must declare the RAG output contract")
        if (
            not isinstance(criteria, list)
            or not criteria
            or not all(isinstance(item, str) and item for item in criteria)
        ):
            raise RagDatasetSetupError(f"Case {case_id} must explain its evidence assertions")


def _request_json(
    client: httpx.Client, method: str, path: str, *, json_body: dict[str, Any] | None = None
) -> Any:
    try:
        response = client.request(method, path, json=json_body)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise RagDatasetSetupError(f"Agent Eval API is unreachable for {method} {path}") from exc
    if response.is_error:
        raise RagDatasetSetupError(
            f"Agent Eval API rejected {method} {path} with HTTP {response.status_code}"
        )
    return response.json()


def _metadata() -> dict[str, Any]:
    return {
        "acceptance_kind": "real_rag_agent",
        "schema_version": DATASET_SCHEMA_VERSION,
        "manifest_sha256": manifest_sha256(),
    }


def _case_fields(case: dict[str, Any]) -> dict[str, Any]:
    return {
        key: case.get(key)
        for key in ("id", "input", "retrieval_context", "output_schema", "criteria", "metadata")
    }


def _find_or_create_dataset(client: httpx.Client, project_id: str) -> tuple[str, str]:
    datasets = _request_json(client, "GET", f"/projects/{project_id}/datasets")
    matches = [item for item in datasets if item.get("name") == DATASET_NAME]
    if len(matches) > 1:
        raise RagDatasetSetupError(f"multiple Datasets are named {DATASET_NAME!r}")
    metadata = _metadata()
    if not matches:
        created = _request_json(
            client,
            "POST",
            f"/projects/{project_id}/datasets",
            json_body={
                "name": DATASET_NAME,
                "description": DATASET_DESCRIPTION,
                "tags": ["live-acceptance", "rag-agent"],
                "cases": RAG_CASES,
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
            (_case_fields(case) for case in version.get("cases", [])),
            key=lambda case: str(case["id"]),
        )
        == sorted((_case_fields(case) for case in RAG_CASES), key=lambda case: str(case["id"]))
    ]
    if matching:
        selected = max(matching, key=lambda item: int(item["version"]))
        return dataset_id, str(selected["id"])
    created = _request_json(
        client,
        "POST",
        f"/projects/{project_id}/datasets/{dataset_id}/versions",
        json_body={"cases": RAG_CASES, "metadata": metadata},
    )
    return dataset_id, str(created["id"])


def _evaluator_identity(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload.get(key) for key in EVALUATOR_DEFINITIONS[0]}


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
            raise RagDatasetSetupError(
                f"multiple Evaluators have identity {definition['name']}:{definition['version']}"
            )
        if matches:
            if _evaluator_identity(matches[0]) != _evaluator_identity(definition):
                raise RagDatasetSetupError(
                    "Evaluator "
                    f"{definition['name']}:{definition['version']} has configuration drift"
                )
            ids.append(str(matches[0]["id"]))
            continue
        created = _request_json(
            client, "POST", f"/projects/{project_id}/evaluators", json_body=definition
        )
        ids.append(str(created["id"]))
    return tuple(ids)


def ensure_rag_acceptance_resources(
    client: httpx.Client, project_id: str
) -> RagAcceptanceResources:
    validate_rag_cases()
    dataset_id, version_id = _find_or_create_dataset(client, project_id)
    evaluator_version_ids = _find_or_create_evaluators(client, project_id)
    return RagAcceptanceResources(
        dataset_id=dataset_id,
        dataset_version_id=version_id,
        evaluator_version_ids=evaluator_version_ids,
        manifest_sha256=manifest_sha256(),
    )
