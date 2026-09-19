from __future__ import annotations

from copy import deepcopy

import httpx
import pytest
from tests.live.rag_dataset import (
    DATASET_NAME,
    EVALUATOR_DEFINITIONS,
    RAG_CASES,
    RagDatasetSetupError,
    ensure_rag_acceptance_resources,
    manifest_sha256,
    validate_rag_cases,
)


def test_rag_cases_have_explainable_retrieval_evidence() -> None:
    validate_rag_cases()
    assert len(RAG_CASES) == 6
    assert len({case["id"] for case in RAG_CASES}) == len(RAG_CASES)
    assert all(case["retrieval_context"] for case in RAG_CASES)
    assert all(case["criteria"] for case in RAG_CASES)


def test_rag_manifest_changes_with_reference_documents() -> None:
    changed = deepcopy(RAG_CASES)
    changed[0]["retrieval_context"][0]["document_id"] = "policy-other"
    assert manifest_sha256(changed) != manifest_sha256()


def test_rag_case_validation_requires_the_output_contract() -> None:
    changed = deepcopy(RAG_CASES)
    changed[0]["output_schema"]["required"] = ["answer"]
    with pytest.raises(RagDatasetSetupError, match="output contract"):
        validate_rag_cases(changed)


def test_rag_resource_setup_creates_then_reuses_frozen_version() -> None:
    datasets: list[dict[str, object]] = []
    versions: list[dict[str, object]] = []
    evaluators: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = __import__("json").loads(request.content) if request.content else None
        if request.method == "GET" and path.endswith("/datasets"):
            return httpx.Response(200, json=datasets)
        if request.method == "POST" and path.endswith("/datasets"):
            dataset = {
                "id": "dataset-1",
                "name": payload["name"],
                "current_version_id": "version-1",
            }
            version = {
                "id": "version-1",
                "version": 1,
                "cases": payload["cases"],
                "metadata": payload["metadata"],
            }
            datasets.append(dataset)
            versions.append(version)
            return httpx.Response(201, json=dataset)
        if request.method == "GET" and path.endswith("/datasets/dataset-1/versions"):
            return httpx.Response(200, json=versions)
        if request.method == "GET" and path.endswith("/evaluators"):
            return httpx.Response(200, json=evaluators)
        if request.method == "POST" and path.endswith("/evaluators"):
            evaluator = {"id": f"evaluator-{len(evaluators) + 1}", **payload, "enabled": True}
            evaluators.append(evaluator)
            return httpx.Response(201, json=evaluator)
        raise AssertionError(f"unexpected request: {request.method} {path}")

    transport = httpx.MockTransport(handler)
    with httpx.Client(base_url="http://platform.test", transport=transport) as client:
        first = ensure_rag_acceptance_resources(client, "project-1")
        second = ensure_rag_acceptance_resources(client, "project-1")

    assert first == second
    assert first.dataset_id == "dataset-1"
    assert first.dataset_version_id == "version-1"
    assert len(first.evaluator_version_ids) == len(EVALUATOR_DEFINITIONS)
    assert datasets[0]["name"] == DATASET_NAME
