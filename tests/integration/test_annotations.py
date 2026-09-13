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
def annotation_client() -> Iterator[tuple[TestClient, Settings, Session]]:
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


def _create_review_run(client: TestClient, settings: Settings) -> tuple[str, str]:
    headers = _headers(settings)
    release = client.post(
        "/projects/project-1/agent-releases",
        json={"label": "review target", "agent_type": "tool", "release_identity": "git:one"},
        headers=headers,
    )
    dataset = client.post(
        "/projects/project-1/datasets",
        json={"name": "review cases", "cases": [{"id": "case-1", "input": "hello"}]},
        headers=headers,
    )
    evaluator = client.post(
        "/projects/project-1/evaluators",
        json={
            "name": "human_quality", "version": "1.0.0", "evaluator_type": "human",
            "requires": [], "supported_agent_types": ["tool"], "score_min": 0,
            "score_max": 1, "direction": "higher_is_better", "default_threshold": 0.8,
        },
        headers=headers,
    )
    assert release.status_code == dataset.status_code == evaluator.status_code == 201
    experiment = client.post(
        "/projects/project-1/experiments",
        json={
            "name": "human review", "agent_version_id": release.json()["id"],
            "dataset_version_id": dataset.json()["current_version_id"],
            "evaluator_version_ids": [evaluator.json()["id"]], "execution_mode": "remote_upload",
        },
        headers=headers,
    )
    assert experiment.status_code == 201
    return experiment.json()["id"], evaluator.json()["id"]


def test_human_review_keeps_an_audit_history(
    annotation_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = annotation_client
    run_id, evaluator_id = _create_review_run(client, settings)
    headers = _headers(settings)
    queue = client.post(
        "/projects/project-1/annotation-queues",
        json={"name": "quality review", "evaluator_version_id": evaluator_id},
        headers=headers,
    )
    assert queue.status_code == 201
    item = client.post(
        f"/projects/project-1/annotation-queues/{queue.json()['id']}/items",
        json={"run_id": run_id, "case_id": "case-1"},
        headers=headers,
    )
    assert item.status_code == 201
    first = client.put(
        f"/projects/project-1/annotation-queues/{queue.json()['id']}/items/{item.json()['id']}/score",
        json={"value": 0.9, "passed": True, "evidence": [{"api_key": "secret", "note": "clear"}]},
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["evidence"] == [
        {"api_key": {"__agent_eval_redacted": True}, "note": "clear"}
    ]
    second = client.put(
        f"/projects/project-1/annotation-queues/{queue.json()['id']}/items/{item.json()['id']}/score",
        json={"value": 0.4, "passed": False},
        headers=headers,
    )
    assert second.status_code == 200
    current = client.get(
        f"/projects/project-1/annotation-queues/{queue.json()['id']}/items/{item.json()['id']}/score",
        headers=headers,
    )
    assert current.status_code == 200
    assert current.json()["id"] == second.json()["id"]
    assert current.json()["value"] == 0.4
    audit = client.get(
        f"/projects/project-1/annotation-queues/{queue.json()['id']}/scores/{second.json()['id']}/audit",
        headers=headers,
    )
    assert audit.status_code == 200
    audit_rows = audit.json()
    assert [entry["action"] for entry in audit_rows] == ["created", "updated"]
    assert [entry["reviewer"] for entry in audit_rows] == ["browser:workspace", "browser:workspace"]
    assert all(entry["created_at"] for entry in audit_rows)
    assert audit_rows[0]["previous_value"] is None
    assert audit_rows[0]["new_value"]["value"] == 0.9
    assert audit_rows[1]["previous_value"]["value"] == 0.9
    assert audit_rows[1]["previous_value"]["passed"] is True
    assert audit_rows[1]["new_value"]["value"] == 0.4
    assert audit_rows[1]["new_value"]["passed"] is False


def test_annotation_queue_requires_human_evaluator(
    annotation_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = annotation_client
    evaluator = client.post(
        "/projects/project-1/evaluators",
        json={
            "name": "exact_match", "version": "1.0.0", "evaluator_type": "deterministic",
            "requires": [], "supported_agent_types": ["tool"], "score_min": 0,
            "score_max": 1, "direction": "higher_is_better", "default_threshold": 1,
        },
        headers=_headers(settings),
    )
    assert evaluator.status_code == 201
    response = client.post(
        "/projects/project-1/annotation-queues",
        json={"name": "invalid", "evaluator_version_id": evaluator.json()["id"]},
        headers=_headers(settings),
    )
    assert response.status_code == 422
