"""Reports are derived from persisted Experiment state, never a platform Agent call."""

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
def report_client() -> Iterator[tuple[TestClient, Settings, Session]]:
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
        [ProjectRecord(id="project-1", name="one"), ProjectRecord(id="project-2", name="two")]
    )
    session.commit()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[auth.get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, settings, session
    session.close()
    engine.dispose()


def _headers(settings: Settings, project_id: str = "project-1") -> dict[str, str]:
    return {"X-Workspace-Session": issue_dev_session(project_id, settings)}


def test_reports_list_remote_experiments_without_agent_endpoint(
    report_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = report_client
    headers = _headers(settings)
    release = client.post(
        "/projects/project-1/agent-releases",
        json={"label": "report release", "agent_type": "tool", "release_identity": "git:report"},
        headers=headers,
    )
    dataset = client.post(
        "/projects/project-1/datasets",
        json={"name": "report cases", "cases": [{"id": "case-1", "input": "hello"}]},
        headers=headers,
    )
    evaluator = client.post(
        "/projects/project-1/evaluators",
        json={
            "name": "success", "version": "1.0.0", "evaluator_type": "deterministic",
            "requires": [], "supported_agent_types": ["tool"], "score_min": 0,
            "score_max": 1, "direction": "higher_is_better", "default_threshold": 1,
        },
        headers=headers,
    )
    assert release.status_code == dataset.status_code == evaluator.status_code == 201
    experiment = client.post(
        "/projects/project-1/experiments",
        json={
            "name": "reportable remote run", "agent_version_id": release.json()["id"],
            "dataset_version_id": dataset.json()["current_version_id"],
            "evaluator_version_ids": [evaluator.json()["id"]], "execution_mode": "remote_upload",
        },
        headers=headers,
    )
    assert experiment.status_code == 201
    reports = client.get("/projects/project-1/reports", headers=headers)
    assert reports.status_code == 200
    assert reports.json()[0]["run_id"] == experiment.json()["id"]
    assert "endpoint_config" not in str(reports.json())
    isolated = client.get("/projects/project-2/reports", headers=_headers(settings, "project-2"))
    assert isolated.status_code == 200
    assert isolated.json() == []
