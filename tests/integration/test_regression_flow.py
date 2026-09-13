"""Regression coverage for the dataset -> remote result -> gate workflow."""

from __future__ import annotations

import base64
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
def regression_client() -> Iterator[tuple[TestClient, Settings, Session]]:
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
    session.add(ProjectRecord(id="project-1", name="Regression project"))
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


def test_imported_dataset_can_create_a_remote_upload_experiment(
    regression_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = regression_client
    headers = _headers(settings)
    dataset = client.post(
        "/projects/project-1/datasets",
        json={"name": "order regression", "cases": []},
        headers=headers,
    )
    assert dataset.status_code == 201
    csv_content = "case_key,prompt\ncancel-ok,Cancel order 42\n"
    imported = client.post(
        f"/projects/project-1/datasets/{dataset.json()['id']}/imports/commit",
        json={
            "format": "csv", "content_base64": base64.b64encode(csv_content.encode()).decode(),
            "field_mapping": {"id": "case_key", "input": "prompt"},
        },
        headers=headers,
    )
    release = client.post(
        "/projects/project-1/agent-releases",
        json={"label": "order agent", "agent_type": "tool", "release_identity": "git:order-v1"},
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
    assert imported.status_code == release.status_code == evaluator.status_code == 201
    experiment = client.post(
        "/projects/project-1/experiments",
        json={
            "name": "remote order regression", "agent_version_id": release.json()["id"],
            "dataset_version_id": imported.json()["dataset_version"]["id"],
            "evaluator_version_ids": [evaluator.json()["id"]], "execution_mode": "remote_upload",
        },
        headers=headers,
    )
    assert experiment.status_code == 201
    manifest = client.get(
        f"/projects/project-1/experiments/{experiment.json()['id']}/manifest", headers=headers
    )
    assert manifest.status_code == 200
    assert [item["case"]["id"] for item in manifest.json()["items"]] == ["cancel-ok"]
