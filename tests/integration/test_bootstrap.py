from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from agent_eval_api import auth
from agent_eval_api.auth import get_db, issue_dev_session, issue_project_key
from agent_eval_api.bootstrap import DEFAULT_PROJECT_ID, LEGACY_PROJECT_ID, ensure_default_project
from agent_eval_api.db import ApiKeyRecord, Base, ProjectRecord
from agent_eval_api.main import create_app
from agent_eval_api.settings import Settings


def trace_payload(trace_id: str) -> dict[str, object]:
    return {
        "trace_id": trace_id,
        "status": "completed",
        "source": "bootstrap-test",
        "spans": [
            {
                "span_id": "agent",
                "trace_id": trace_id,
                "kind": "agent",
                "name": "bootstrap-agent",
                "status": "completed",
                "started_at": datetime.now(UTC).isoformat(),
            }
        ],
    }


@pytest.fixture
def bootstrapped_client() -> Iterator[tuple[TestClient, Settings, Session, object]]:
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
    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[auth.get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, settings, session, engine
    session.close()
    engine.dispose()


def test_fresh_database_bootstraps_default_project_and_accepts_first_trace(
    bootstrapped_client: tuple[TestClient, Settings, Session, object],
) -> None:
    client, settings, session, _ = bootstrapped_client

    ensure_default_project(lambda: session)

    assert session.get(ProjectRecord, DEFAULT_PROJECT_ID) is not None
    response = client.post(
        f"/projects/{DEFAULT_PROJECT_ID}/traces",
        json=trace_payload("fresh-trace"),
        headers={"X-Workspace-Session": issue_dev_session(DEFAULT_PROJECT_ID, settings)},
    )
    assert response.status_code == 201
    assert response.json()["trace_id"] == "fresh-trace"


def test_legacy_project_and_key_migrate_without_losing_authenticated_ingestion(
    bootstrapped_client: tuple[TestClient, Settings, Session, object],
) -> None:
    client, settings, session, _ = bootstrapped_client
    session.add(ProjectRecord(id=LEGACY_PROJECT_ID, name="Legacy project"))
    raw_key, key_record = issue_project_key(LEGACY_PROJECT_ID, settings)
    session.add(key_record)
    session.commit()
    key_id = key_record.id

    ensure_default_project(lambda: session)

    assert session.get(ProjectRecord, LEGACY_PROJECT_ID) is None
    assert session.get(ProjectRecord, DEFAULT_PROJECT_ID) is not None
    migrated_key = session.get(ApiKeyRecord, key_id)
    assert migrated_key is not None
    assert migrated_key.project_id == DEFAULT_PROJECT_ID

    response = client.post(
        f"/projects/{DEFAULT_PROJECT_ID}/traces",
        json=trace_payload("upgraded-trace"),
        headers={"X-Project-Key": raw_key},
    )
    assert response.status_code == 201

    legacy_session = client.get(
        f"/projects/{DEFAULT_PROJECT_ID}/access-check",
        headers={"X-Workspace-Session": issue_dev_session(LEGACY_PROJECT_ID, settings)},
    )
    assert legacy_session.status_code == 200
