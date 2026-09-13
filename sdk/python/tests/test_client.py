from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from agent_eval import AgentEvalApiError, Client, IncompatibleServiceError
from agent_eval.models import Dataset

NOW = datetime.now(UTC).isoformat()


def dataset_payload() -> dict[str, Any]:
    return {
        "id": "dataset-1",
        "project_id": "project-1",
        "name": "orders",
        "description": None,
        "tags": [],
        "current_version_id": "version-2",
        "created_at": NOW,
        "updated_at": NOW,
    }


def version_payload(version: int) -> dict[str, Any]:
    return {
        "id": f"version-{version}",
        "dataset_id": "dataset-1",
        "version": version,
        "cases": [
            {
                "id": "case-1",
                "input": {"order_id": "42"},
                "variables": {},
                "expected_output": None,
                "output_schema": None,
                "criteria": [],
                "expected_tools": [],
                "expected_state": {"status": "cancelled"},
                "retrieval_context": [],
                "messages": [],
                "metadata": {},
                "source_trace_id": None,
                "source_span_ids": [],
                "source_mapping": {},
            }
        ],
        "metadata": {},
        "created_at": NOW,
    }


def release_payload() -> dict[str, Any]:
    return {
        "id": "release-1",
        "project_id": "project-1",
        "version": 1,
        "label": "candidate",
        "agent_type": "tool",
        "release_identity": "git:abc",
        "source_revision": "abc",
        "metadata": {},
        "enabled": True,
        "created_at": NOW,
    }


def client_with(handler: Any, *, key: str = "aek_secret_value") -> Client:
    return Client(
        base_url="https://eval.example.test",
        project_id="project-1",
        api_key=key,
        transport=httpx.MockTransport(handler),
    )


def test_client_requires_configuration_and_redacts_api_errors() -> None:
    with pytest.raises(ValueError, match="required"):
        Client(base_url="", project_id="", api_key="")

    secret = "aek_never_print_this"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Project-Key"] == secret
        return httpx.Response(401, json={"detail": secret})

    with (
        client_with(handler, key=secret) as client,
        pytest.raises(AgentEvalApiError) as caught,
    ):
        client.list_datasets()
    assert secret not in str(caught.value)
    assert "401" in str(caught.value)


def test_compatibility_fails_before_any_mutating_request() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(
            200,
            json={"contract": "agent-eval-sdk", "major_version": 2, "service_version": "9"},
        )

    with (
        client_with(handler) as client,
        pytest.raises(IncompatibleServiceError, match="major 1"),
    ):
        client.register_release(
            label="must-not-write",
            agent_type="tool",
            release_identity="git:blocked",
        )
    assert methods == ["GET"]


def test_dataset_selection_requires_an_immutable_version() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/datasets"):
            return httpx.Response(200, json=[dataset_payload()])
        return httpx.Response(200, json=[version_payload(1), version_payload(2)])

    with client_with(handler) as client:
        with pytest.raises(ValueError, match="exactly one immutable"):
            client.get_dataset("orders")
        selected = client.get_dataset("orders", version=1)

    assert selected.version.id == "version-1"
    assert selected.version.id != selected.dataset.current_version_id


def test_release_experiment_and_manifest_contracts_are_typed() -> None:
    requests: list[tuple[str, str, dict[str, Any] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = None if not request.content else __import__("json").loads(request.content)
        requests.append((request.method, request.url.path, body))
        if request.url.path.endswith("/sdk-contract"):
            return httpx.Response(
                200,
                json={
                    "contract": "agent-eval-sdk",
                    "major_version": 1,
                    "service_version": "0.1.0",
                },
            )
        if request.url.path.endswith("/agent-releases"):
            return httpx.Response(201, json=release_payload())
        if request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": "experiment-1",
                    "name": "candidate run",
                    "agent_version_id": "release-1",
                    "dataset_version_id": "version-1",
                    "evaluator_version_ids": ["eval-1"],
                    "execution_mode": "sdk_task",
                    "evidence_policy": "tool_trajectory_required",
                    "status": "queued",
                    "total_cases": 1,
                    "completed_cases": 0,
                    "failed_cases": 0,
                    "baseline_run_id": None,
                    "execution_options": {"repetitions": 1},
                    "configuration_snapshot": {
                        "agent_version": {"release_identity": "git:abc"}
                    },
                    "created_at": NOW,
                    "started_at": None,
                    "finished_at": None,
                },
            )
        return httpx.Response(
            200,
            json={
                "experiment_id": "experiment-1",
                "dataset_version_id": "version-1",
                "items": [{"case": version_payload(1)["cases"][0], "attempts": []}],
                "total": 1,
                "offset": 0,
                "limit": 50,
                "next_offset": None,
            },
        )

    with client_with(handler) as client:
        release = client.register_release(
            label="candidate",
            agent_type="tool",
            release_identity="git:abc",
            source_revision="abc",
        )
        experiment = client.create_experiment(
            name="candidate run",
            dataset_version_id="version-1",
            release_id=release.id,
            evaluator_version_ids=["eval-1"],
            evidence_policy="tool_trajectory_required",
        )
        manifest = client.get_manifest_page(experiment.id)

    assert experiment.configuration_snapshot["agent_version"]["release_identity"] == "git:abc"
    assert manifest.items[0].case.id == "case-1"
    request_body = requests[2][2]
    assert request_body is not None
    assert request_body["execution_mode"] == "sdk_task"


def test_release_catalog_resolves_an_endpoint_independent_release() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sdk-contract"):
            return httpx.Response(
                200,
                json={
                    "contract": "agent-eval-sdk",
                    "major_version": 1,
                    "service_version": "0.1.0",
                },
            )
        return httpx.Response(200, json=[release_payload()])

    with client_with(handler) as client:
        assert client.get_release("release-1").release_identity == "git:abc"
        with pytest.raises(ValueError, match="exactly one"):
            client.get_release("missing")


def test_models_reject_server_field_drift() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Dataset.model_validate({**dataset_payload(), "unexpected_server_field": True})
