import base64
import csv
import io
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from agent_eval_api import auth
from agent_eval_api.auth import get_db, issue_dev_session
from agent_eval_api.dataset_import import DatasetImportFormat, parse_dataset_bytes
from agent_eval_api.db import Base, ProjectRecord
from agent_eval_api.main import create_app
from agent_eval_api.settings import Settings


@pytest.fixture
def dataset_client() -> Iterator[tuple[TestClient, Settings, Session]]:
    settings = Settings(
        database_url="sqlite:///:memory:",
        api_key_salt="test-salt",
        workspace_session_secret="test-session",
    )
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(
        [
            ProjectRecord(id="project-1", name="one"),
            ProjectRecord(id="project-2", name="two"),
        ]
    )
    session.commit()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[auth.get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, settings, session
    session.close()


def headers(settings: Settings, project_id: str = "project-1") -> dict[str, str]:
    return {"X-Workspace-Session": issue_dev_session(project_id, settings)}


def cases() -> list[dict[str, object]]:
    return [
        {
            "id": "prompt-1",
            "input": "What is the order status?",
            "variables": {"order_id": "42"},
            "expected_output": "shipped",
            "criteria": ["Answer with the current status."],
            "metadata": {"category": "prompt", "difficulty": "easy"},
        },
        {
            "id": "rag-1",
            "input": {"question": "Where is order 42?"},
            "expected_output": {"status": "shipped"},
            "output_schema": {"type": "object"},
            "retrieval_context": [
                {"content": "Order 42 shipped yesterday.", "document_id": "orders-42"}
            ],
            "metadata": {"category": "rag"},
        },
        {
            "id": "tool-1",
            "input": "Cancel order 42",
            "expected_tools": [
                {"name": "search_order", "arguments": {"order_id": "42"}, "order": 0},
                {"name": "cancel_order", "arguments": {"order_id": "42"}, "order": 1},
            ],
            "expected_state": {"status": "cancelled"},
            "metadata": {"category": "tool"},
        },
        {
            "id": "chat-1",
            "input": "Continue",
            "messages": [
                {"role": "user", "content": "My order is late."},
                {"role": "assistant", "content": "Which order number?"},
                {"role": "user", "content": "42"},
            ],
            "metadata": {"category": "chat"},
        },
    ]


def test_create_and_read_versioned_dataset_with_all_case_shapes(
    dataset_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = dataset_client
    created = client.post(
        "/projects/project-1/datasets",
        json={
            "name": "Order quality suite",
            "description": "Regression cases",
            "tags": ["orders", "release"],
            "metadata": {"owner": "support"},
            "cases": cases(),
        },
        headers=headers(settings),
    )

    assert created.status_code == 201
    dataset = created.json()
    assert dataset["tags"] == ["orders", "release"]

    versions = client.get(
        f"/projects/project-1/datasets/{dataset['id']}/versions", headers=headers(settings)
    )
    assert versions.status_code == 200
    version = versions.json()[0]
    assert version["version"] == 1
    assert version["metadata"] == {"owner": "support"}
    assert {case["id"] for case in version["cases"]} == {"prompt-1", "rag-1", "tool-1", "chat-1"}
    tool_case = next(case for case in version["cases"] if case["id"] == "tool-1")
    assert tool_case["expected_tools"][1]["name"] == "cancel_order"


def test_case_edits_create_new_versions_without_mutating_history(
    dataset_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = dataset_client
    created = client.post(
        "/projects/project-1/datasets",
        json={"name": "Orders", "cases": [cases()[0]]},
        headers=headers(settings),
    )
    dataset_id = created.json()["id"]
    version_id = created.json()["current_version_id"]

    appended = client.post(
        f"/projects/project-1/datasets/{dataset_id}/versions/{version_id}/cases",
        json=cases()[2],
        headers=headers(settings),
    )
    assert appended.status_code == 201
    assert appended.json()["version"] == 2
    assert len(appended.json()["cases"]) == 2

    updated_case = {**cases()[0], "expected_output": "delivered"}
    updated = client.patch(
        f"/projects/project-1/datasets/{dataset_id}/versions/{appended.json()['id']}/cases/prompt-1",
        json=updated_case,
        headers=headers(settings),
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 3

    original = client.get(
        f"/projects/project-1/datasets/{dataset_id}/versions/{version_id}",
        headers=headers(settings),
    )
    assert original.status_code == 200
    assert original.json()["cases"][0]["expected_output"] == "shipped"
    assert len(original.json()["cases"]) == 1


def test_archiving_case_creates_version_without_mutating_history(
    dataset_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = dataset_client
    created = client.post(
        "/projects/project-1/datasets",
        json={"name": "Orders", "cases": cases()[:2]},
        headers=headers(settings),
    )
    assert created.status_code == 201
    dataset = created.json()
    version_id = dataset["current_version_id"]

    archived = client.delete(
        f"/projects/project-1/datasets/{dataset['id']}/versions/{version_id}/cases/rag-1",
        headers=headers(settings),
    )

    assert archived.status_code == 201
    assert archived.json()["version"] == 2
    assert [case["id"] for case in archived.json()["cases"]] == ["prompt-1"]

    original = client.get(
        f"/projects/project-1/datasets/{dataset['id']}/versions/{version_id}",
        headers=headers(settings),
    )
    assert original.status_code == 200
    assert {case["id"] for case in original.json()["cases"]} == {"prompt-1", "rag-1"}

    missing = client.delete(
        f"/projects/project-1/datasets/{dataset['id']}/versions/{archived.json()['id']}/cases/rag-1",
        headers=headers(settings),
    )
    assert missing.status_code == 404


def test_dataset_metadata_and_project_boundary_are_enforced(
    dataset_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = dataset_client
    created = client.post(
        "/projects/project-1/datasets",
        json={"name": "Orders", "tags": ["baseline"], "cases": []},
        headers=headers(settings),
    )
    dataset_id = created.json()["id"]

    changed = client.patch(
        f"/projects/project-1/datasets/{dataset_id}",
        json={"description": "Updated", "tags": ["candidate"]},
        headers=headers(settings),
    )
    assert changed.status_code == 200
    assert changed.json()["description"] == "Updated"
    assert changed.json()["tags"] == ["candidate"]

    forbidden = client.get(
        f"/projects/project-2/datasets/{dataset_id}", headers=headers(settings, "project-1")
    )
    assert forbidden.status_code == 401

    invalid = client.post(
        "/projects/project-1/datasets",
        json={"name": "Bad", "tags": ["duplicate", "duplicate"]},
        headers=headers(settings),
    )
    assert invalid.status_code == 422


def test_import_preview_mapping_partial_commit_and_cancel_do_not_mutate_early(
    dataset_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = dataset_client
    created = client.post(
        "/projects/project-1/datasets",
        json={"name": "Imported cases", "cases": []},
        headers=headers(settings),
    )
    dataset_id = created.json()["id"]
    content = base64.b64encode(
        b"case_key,prompt,tools\nvalid-1,Find order,[]\nvalid-1,Duplicate,[]\n"
    ).decode()
    payload = {
        "format": "csv",
        "content_base64": content,
        "field_mapping": {"id": "case_key", "input": "prompt", "expected_tools": "tools"},
    }

    preview = client.post(
        f"/projects/project-1/datasets/{dataset_id}/imports/preview",
        json=payload,
        headers=headers(settings),
    )
    assert preview.status_code == 200
    assert len(preview.json()["cases"]) == 1
    assert preview.json()["issues"][0]["line"] == 3

    cancelled = client.post(
        f"/projects/project-1/datasets/{dataset_id}/imports/cancel", headers=headers(settings)
    )
    assert cancelled.status_code == 204
    versions_before_commit = client.get(
        f"/projects/project-1/datasets/{dataset_id}/versions", headers=headers(settings)
    )
    assert len(versions_before_commit.json()) == 1

    rejected = client.post(
        f"/projects/project-1/datasets/{dataset_id}/imports/commit",
        json=payload,
        headers=headers(settings),
    )
    assert rejected.status_code == 422
    assert len(rejected.json()["detail"]["issues"]) == 1

    committed = client.post(
        f"/projects/project-1/datasets/{dataset_id}/imports/commit",
        json={**payload, "allow_partial": True},
        headers=headers(settings),
    )
    assert committed.status_code == 201
    assert committed.json()["dataset_version"]["version"] == 2
    assert committed.json()["dataset_version"]["cases"][0]["id"] == "valid-1"
    assert len(committed.json()["issues"]) == 1


def test_dataset_version_exports_round_trip_structured_cases_and_metadata(
    dataset_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = dataset_client
    created = client.post(
        "/projects/project-1/datasets",
        json={
            "name": "Order quality suite",
            "description": "Regression cases",
            "tags": ["orders"],
            "metadata": {"owner": "support"},
            "cases": cases(),
        },
        headers=headers(settings),
    ).json()
    version_id = created["current_version_id"]
    expected_cases = client.get(
        f"/projects/project-1/datasets/{created['id']}/versions/{version_id}",
        headers=headers(settings),
    ).json()["cases"]

    jsonl_export = client.get(
        f"/projects/project-1/datasets/{created['id']}/versions/{version_id}/export?format=jsonl",
        headers=headers(settings),
    )
    assert jsonl_export.status_code == 200
    assert jsonl_export.headers["content-type"].startswith("application/x-ndjson")
    jsonl_metadata = json.loads(jsonl_export.text.splitlines()[0])["__agent_eval_export_metadata"]
    assert jsonl_metadata["dataset_version"]["id"] == version_id
    assert jsonl_metadata["dataset_version"]["metadata"] == {"owner": "support"}
    jsonl_round_trip = parse_dataset_bytes(
        jsonl_export.content, DatasetImportFormat.JSONL
    )
    assert jsonl_round_trip.issues == []
    assert [case.model_dump(mode="json") for case in jsonl_round_trip.cases] == expected_cases

    csv_export = client.get(
        f"/projects/project-1/datasets/{created['id']}/export?format=csv",
        headers=headers(settings),
    )
    assert csv_export.status_code == 200
    rows = list(csv.DictReader(io.StringIO(csv_export.text)))
    csv_metadata = json.loads(rows[0]["__agent_eval_export_metadata"])
    assert csv_metadata["dataset_version"]["id"] == version_id
    csv_round_trip = parse_dataset_bytes(csv_export.content, DatasetImportFormat.CSV)
    assert csv_round_trip.issues == []
    assert [case.model_dump(mode="json") for case in csv_round_trip.cases] == expected_cases


def test_trace_can_create_a_versioned_dataset_case(
    dataset_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = dataset_client
    dataset = client.post(
        "/projects/project-1/datasets",
        json={"name": "Trace-derived cases", "cases": []},
        headers=headers(settings),
    ).json()
    started_at = datetime.now(UTC)
    trace = client.post(
        "/projects/project-1/traces",
        json={
            "trace_id": "trace-order-42",
            "status": "completed",
            "spans": [
                {
                    "span_id": "agent",
                    "trace_id": "trace-order-42",
                    "kind": "agent",
                    "name": "order-agent",
                    "status": "completed",
                    "started_at": started_at.isoformat(),
                    "input": {"request": "Where is order 42?"},
                },
                {
                    "span_id": "tool",
                    "trace_id": "trace-order-42",
                    "parent_span_id": "agent",
                    "kind": "tool",
                    "name": "search_order",
                    "status": "completed",
                    "started_at": (started_at + timedelta(seconds=1)).isoformat(),
                    "input": {"order_id": "42"},
                    "attributes": {"tool.name": "search_order"},
                },
                {
                    "span_id": "result",
                    "trace_id": "trace-order-42",
                    "parent_span_id": "tool",
                    "kind": "tool_result",
                    "name": "search_order result",
                    "status": "completed",
                    "started_at": (started_at + timedelta(seconds=2)).isoformat(),
                    "output": {"status": "shipped"},
                },
            ],
        },
        headers=headers(settings),
    )
    assert trace.status_code == 201

    created = client.post(
        f"/projects/project-1/datasets/{dataset['id']}/versions/{dataset['current_version_id']}/cases/from-trace",
        json={
            "id": "trace-case-42",
            "trace_id": "trace-order-42",
            "input": {"span_id": "agent", "field": "input"},
            "expected_output": {"span_id": "result", "field": "output"},
            "expected_state": {"span_id": "result", "field": "output"},
            "tool_span_ids": ["tool"],
            "metadata": {"category": "orders"},
            "metadata_mapping": {
                "tool_name": {
                    "span_id": "tool",
                    "field": "attributes",
                    "attribute_key": "tool.name",
                }
            },
        },
        headers=headers(settings),
    )

    assert created.status_code == 201
    assert created.json()["version"] == 2
    case = created.json()["cases"][0]
    assert case["input"] == {"request": "Where is order 42?"}
    assert case["expected_output"] == {"status": "shipped"}
    assert case["expected_state"] == {"status": "shipped"}
    assert case["expected_tools"] == [
        {"name": "search_order", "arguments": {"order_id": "42"}, "order": 0}
    ]
    assert case["source_trace_id"] == "trace-order-42"
    assert case["source_span_ids"] == ["agent", "result", "tool"]
    assert case["source_mapping"] == {
        "input": {
            "span_id": "agent",
            "field": "input",
            "attribute_key": None,
        },
        "expected_output": {
            "span_id": "result",
            "field": "output",
            "attribute_key": None,
        },
        "expected_state": {
            "span_id": "result",
            "field": "output",
            "attribute_key": None,
        },
        "tool_span_ids": ["tool"],
        "metadata_mapping": {
            "tool_name": {
                "span_id": "tool",
                "field": "attributes",
                "attribute_key": "tool.name",
            }
        },
    }
    assert case["metadata"] == {
        "category": "orders",
        "tool_name": "search_order",
        "source": "trace",
    }


def test_trace_without_output_creates_case_with_optional_expected_output(
    dataset_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = dataset_client
    dataset = client.post(
        "/projects/project-1/datasets",
        json={"name": "Input-only traces", "cases": []},
        headers=headers(settings),
    ).json()
    started_at = datetime.now(UTC)
    trace = client.post(
        "/projects/project-1/traces",
        json={
            "trace_id": "trace-input-only",
            "status": "completed",
            "spans": [
                {
                    "span_id": "agent-input-only",
                    "trace_id": "trace-input-only",
                    "kind": "agent",
                    "name": "input-only-agent",
                    "status": "completed",
                    "started_at": started_at.isoformat(),
                    "input": {"question": "What is order 42?"},
                }
            ],
        },
        headers=headers(settings),
    )
    assert trace.status_code == 201

    created = client.post(
        f"/projects/project-1/datasets/{dataset['id']}/versions/{dataset['current_version_id']}/cases/from-trace",
        json={"id": "trace-input-only-case", "trace_id": "trace-input-only"},
        headers=headers(settings),
    )

    assert created.status_code == 201
    case = created.json()["cases"][0]
    assert case["input"] == {"question": "What is order 42?"}
    assert case["expected_output"] is None
    assert case["source_trace_id"] == "trace-input-only"
    assert case["source_span_ids"] == ["agent-input-only"]
