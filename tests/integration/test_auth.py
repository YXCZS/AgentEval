from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from agent_eval_api.auth import get_db, issue_dev_session, issue_project_key
from agent_eval_api.db import ApiKeyRecord, Base, ProjectRecord
from agent_eval_api.main import create_app
from agent_eval_api.settings import Settings


@pytest.fixture
def auth_client() -> Iterator[tuple[TestClient, Settings, Session]]:
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
    from agent_eval_api import auth

    app.dependency_overrides[auth.get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, settings, session
    session.close()


def test_project_key_is_scoped_and_plaintext_is_not_persisted(
    auth_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = auth_client
    raw_key, record = issue_project_key("project-1", settings)
    session.add(record)
    session.commit()

    response = client.get("/projects/project-1/access-check", headers={"X-Project-Key": raw_key})
    assert response.status_code == 200
    assert response.json() == {"project_id": "project-1", "principal_type": "agent"}
    assert raw_key not in record.key_hash

    cross_project = client.get(
        "/projects/project-2/access-check", headers={"X-Project-Key": raw_key}
    )
    assert cross_project.status_code == 401


def test_browser_session_is_bound_to_project(
    auth_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = auth_client
    session_token = issue_dev_session("project-1", settings)

    response = client.get(
        "/projects/project-1/access-check", headers={"X-Workspace-Session": session_token}
    )
    assert response.status_code == 200
    assert response.json()["principal_type"] == "browser"

    cross_project = client.get(
        "/projects/project-2/access-check", headers={"X-Workspace-Session": session_token}
    )
    assert cross_project.status_code == 401


def test_missing_credentials_are_rejected(
    auth_client: tuple[TestClient, Settings, Session],
) -> None:
    client, _, _ = auth_client

    response = client.get("/projects/project-1/access-check")

    assert response.status_code == 401


def test_credentials_for_nonexistent_project_are_rejected(
    auth_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = auth_client

    response = client.get(
        "/projects/missing-project/access-check",
        headers={"X-Workspace-Session": issue_dev_session("missing-project", settings)},
    )

    assert response.status_code == 401


def test_browser_can_create_list_and_revoke_project_key(
    auth_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = auth_client
    headers = {"X-Workspace-Session": issue_dev_session("project-1", settings)}

    created = client.post(
        "/projects/project-1/api-keys",
        headers=headers,
        json={"name": "local-sdk"},
    )

    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "local-sdk"
    assert body["key"].startswith("aek_project-1_")
    assert session.scalar(select(ApiKeyRecord).where(ApiKeyRecord.id == body["id"])).key_hash

    listed = client.get("/projects/project-1/api-keys", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["key_prefix"] == body["key_prefix"]
    assert "key" not in listed.json()[0]

    revoked = client.delete(
        f"/projects/project-1/api-keys/{body['id']}", headers=headers
    )
    assert revoked.status_code == 204
    denied = client.get(
        "/projects/project-1/access-check", headers={"X-Project-Key": body["key"]}
    )
    assert denied.status_code == 401


def test_agent_key_cannot_manage_project_keys(
    auth_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = auth_client
    raw_key, record = issue_project_key("project-1", settings)
    session.add(record)
    session.commit()

    response = client.get(
        "/projects/project-1/api-keys", headers={"X-Project-Key": raw_key}
    )

    assert response.status_code == 403
