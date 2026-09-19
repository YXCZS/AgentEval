from __future__ import annotations

from copy import deepcopy

import httpx
import pytest
from tests.live.tool_dataset import (
    DATASET_NAME,
    EVALUATOR_DEFINITIONS,
    TOOL_CASES,
    ToolDatasetSetupError,
    ensure_tool_acceptance_resources,
    manifest_sha256,
    validate_tool_cases,
)


def test_tool_cases_have_explainable_business_evidence() -> None:
    validate_tool_cases()
    assert len(TOOL_CASES) == 25
    assert len({case["id"] for case in TOOL_CASES}) == len(TOOL_CASES)
    assert all(case["criteria"] for case in TOOL_CASES)
    assert all(case["expected_state"] for case in TOOL_CASES)
    assert all(len(case["expected_tools"]) == 1 for case in TOOL_CASES)


def test_manifest_hash_changes_with_business_expectations() -> None:
    original = manifest_sha256()
    changed = deepcopy(TOOL_CASES)
    changed[0]["expected_state"]["status"] = "different"
    assert manifest_sha256(changed) != original


def test_validation_rejects_expected_tool_for_another_order() -> None:
    changed = deepcopy(TOOL_CASES)
    changed[0]["expected_tools"][0]["arguments"]["order_id"] = "ORDER-9999"
    with pytest.raises(ToolDatasetSetupError, match="must target its order"):
        validate_tool_cases(changed)


def test_resource_setup_creates_then_reuses_the_frozen_version() -> None:
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

    with httpx.Client(
        base_url="http://platform.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        first = ensure_tool_acceptance_resources(client, "project-1")
        second = ensure_tool_acceptance_resources(client, "project-1")

    assert first == second
    assert first.dataset_id == "dataset-1"
    assert first.dataset_version_id == "version-1"
    assert len(first.evaluator_version_ids) == len(EVALUATOR_DEFINITIONS)
    assert len(versions) == 1
    assert len(evaluators) == len(EVALUATOR_DEFINITIONS)
    assert datasets[0]["name"] == DATASET_NAME


def test_resource_setup_creates_new_version_when_manifest_changed() -> None:
    datasets = [{"id": "dataset-1", "name": DATASET_NAME, "current_version_id": "version-old"}]
    versions = [
        {
            "id": "version-old",
            "version": 1,
            "cases": [],
            "metadata": {"manifest_sha256": "old"},
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = __import__("json").loads(request.content) if request.content else None
        if request.method == "GET" and path.endswith("/datasets"):
            return httpx.Response(200, json=datasets)
        if request.method == "GET" and path.endswith("/datasets/dataset-1/versions"):
            return httpx.Response(200, json=versions)
        if request.method == "POST" and path.endswith("/datasets/dataset-1/versions"):
            created = {"id": "version-2", "version": 2, **payload}
            versions.append(created)
            return httpx.Response(201, json=created)
        if request.method == "GET" and path.endswith("/evaluators"):
            return httpx.Response(200, json=[])
        if request.method == "POST" and path.endswith("/evaluators"):
            return httpx.Response(201, json={"id": payload["name"], **payload})
        raise AssertionError(f"unexpected request: {request.method} {path}")

    with httpx.Client(
        base_url="http://platform.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        resources = ensure_tool_acceptance_resources(client, "project-1")

    assert resources.dataset_version_id == "version-2"
    assert len(versions) == 2


def test_resource_setup_rejects_versioned_evaluator_drift() -> None:
    drifted = {"id": "evaluator-1", **EVALUATOR_DEFINITIONS[0], "enabled": True}
    drifted["config"] = {"metric": "exact_match"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = __import__("json").loads(request.content) if request.content else None
        if request.method == "GET" and path.endswith("/datasets"):
            return httpx.Response(200, json=[])
        if request.method == "POST" and path.endswith("/datasets"):
            return httpx.Response(
                201,
                json={"id": "dataset-1", "current_version_id": "version-1"},
            )
        if request.method == "GET" and path.endswith("/evaluators"):
            return httpx.Response(200, json=[drifted])
        raise AssertionError(f"unexpected request: {request.method} {path} {payload}")

    with httpx.Client(
        base_url="http://platform.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ToolDatasetSetupError, match="configuration drift"):
            ensure_tool_acceptance_resources(client, "project-1")
