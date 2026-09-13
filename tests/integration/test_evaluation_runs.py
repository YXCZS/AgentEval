"""Experiment control-plane contracts for user-owned runtime execution."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from agent_eval_api import auth
from agent_eval_api.auth import get_db, issue_dev_session
from agent_eval_api.db import Base, ProjectRecord
from agent_eval_api.main import create_app
from agent_eval_api.settings import Settings


@pytest.fixture
def run_client() -> Iterator[tuple[TestClient, Settings, Session]]:
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
    session.add(ProjectRecord(id="project-1", name="one"))
    session.commit()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[auth.get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, settings, session
    session.close()
    engine.dispose()


def _headers(settings: Settings) -> dict[str, str]:
    return {"X-Workspace-Session": issue_dev_session("project-1", settings)}


def _resources(client: TestClient, settings: Settings) -> tuple[str, str, str]:
    headers = _headers(settings)
    release = client.post(
        "/projects/project-1/agent-releases",
        json={
            "label": "SDK candidate", "agent_type": "tool", "release_identity": "git:abc",
            "source_revision": "abc", "metadata": {"runtime": "user-owned"},
        },
        headers=headers,
    )
    dataset = client.post(
        "/projects/project-1/datasets",
        json={
            "name": "order checks",
            "cases": [{"id": "cancel-42", "input": "Cancel order 42"}],
        },
        headers=headers,
    )
    evaluator = client.post(
        "/projects/project-1/evaluators",
        json={
            "name": "task_success", "version": "1.0.0", "evaluator_type": "deterministic",
            "requires": [], "supported_agent_types": ["tool"], "score_min": 0,
            "score_max": 1, "direction": "higher_is_better", "default_threshold": 1,
        },
        headers=headers,
    )
    assert release.status_code == dataset.status_code == evaluator.status_code == 201
    return release.json()["id"], dataset.json()["current_version_id"], evaluator.json()["id"]


def test_sdk_contract_is_authenticated_and_versioned(
    run_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = run_client
    assert client.get("/projects/project-1/sdk-contract").status_code == 401
    response = client.get("/projects/project-1/sdk-contract", headers=_headers(settings))
    assert response.status_code == 200
    assert response.json()["contract"] == "agent-eval-sdk"


def test_remote_upload_experiment_freezes_an_endpoint_independent_release(
    run_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = run_client
    release_id, dataset_version_id, evaluator_id = _resources(client, settings)
    created = client.post(
        "/projects/project-1/experiments",
        json={
            "name": "Remote runtime snapshot", "agent_version_id": release_id,
            "dataset_version_id": dataset_version_id, "evaluator_version_ids": [evaluator_id],
            "execution_mode": "remote_upload", "evidence_policy": "trace_required",
        },
        headers=_headers(settings),
    )
    assert created.status_code == 201
    snapshot = created.json()["configuration_snapshot"]
    assert snapshot["agent_version"]["release_identity"] == "git:abc"
    assert "endpoint_config" not in snapshot["agent_version"]
    assert snapshot["dataset_version"]["id"] == dataset_version_id


def test_experiment_list_filters_and_pages_are_project_scoped(
    run_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = run_client
    release_id, dataset_version_id, evaluator_id = _resources(client, settings)
    for name, mode in [("baseline release", "sdk_task"), ("candidate release", "remote_upload")]:
        response = client.post(
            "/projects/project-1/experiments",
            json={
                "name": name,
                "agent_version_id": release_id,
                "dataset_version_id": dataset_version_id,
                "evaluator_version_ids": [evaluator_id],
                "execution_mode": mode,
            },
            headers=_headers(settings),
        )
        assert response.status_code == 201

    page = client.get(
        "/projects/project-1/experiments?query=release&limit=1&offset=1",
        headers=_headers(settings),
    )
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert page.json()["offset"] == 1
    assert page.json()["next_offset"] is None
    assert len(page.json()["items"]) == 1

    filtered = client.get(
        "/projects/project-1/experiments?execution_mode=remote_upload",
        headers=_headers(settings),
    )
    assert filtered.status_code == 200
    assert [item["name"] for item in filtered.json()["items"]] == ["candidate release"]

    session.add(ProjectRecord(id="project-2", name="two"))
    session.commit()
    forbidden = client.get(
        "/projects/project-2/experiments?query=release",
        headers=_headers(settings),
    )
    assert forbidden.status_code == 401


def test_remote_runtime_reads_manifest_and_uploads_its_own_result(
    run_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = run_client
    release_id, dataset_version_id, evaluator_id = _resources(client, settings)
    created = client.post(
        "/projects/project-1/experiments",
        json={
            "name": "Node remote upload", "agent_version_id": release_id,
            "dataset_version_id": dataset_version_id, "evaluator_version_ids": [evaluator_id],
            "execution_mode": "remote_upload",
        },
        headers=_headers(settings),
    )
    assert created.status_code == 201
    experiment_id = created.json()["id"]
    manifest = client.get(
        f"/projects/project-1/experiments/{experiment_id}/manifest", headers=_headers(settings)
    )
    assert manifest.status_code == 200
    assert manifest.json()["items"][0]["case"]["id"] == "cancel-42"
    started = client.post(
        f"/projects/project-1/experiments/{experiment_id}/items/start",
        json={"case_id": "cancel-42", "external_run_id": "nodejs-42"},
        headers=_headers(settings),
    )
    assert started.status_code == 201
    completed = client.post(
        f"/projects/project-1/experiments/{experiment_id}/items/{started.json()['id']}/complete",
        json={"output": {"status": "cancelled"}, "runtime_metadata": {"runtime": "nodejs"}},
        headers=_headers(settings),
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["runtime_metadata"] == {"runtime": "nodejs"}
