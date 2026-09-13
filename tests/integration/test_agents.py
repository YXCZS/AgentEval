from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from agent_eval_api.auth import get_db, issue_dev_session
from agent_eval_api.db import Base, ProjectRecord
from agent_eval_api.main import create_app
from agent_eval_api.settings import Settings


@pytest.fixture
def agent_client() -> Iterator[tuple[TestClient, Settings]]:
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
    from agent_eval_api import auth

    app.dependency_overrides[auth.get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, settings
    session.close()


def headers(settings: Settings) -> dict[str, str]:
    return {"X-Workspace-Session": issue_dev_session("project-1", settings)}


def test_legacy_managed_agent_routes_are_removed(
    agent_client: tuple[TestClient, Settings],
) -> None:
    client, settings = agent_client
    response = client.post(
        "/projects/project-1/agents",
        json={
            "name": "removed prompt agent",
            "agent_type": "prompt",
            "prompt_config": {
                "model": "removed",
                "endpoint": "https://llm.example.test/v1/chat/completions",
            },
        },
        headers=headers(settings),
    )

    assert response.status_code == 404


def test_legacy_agent_connection_routes_are_removed(
    agent_client: tuple[TestClient, Settings],
) -> None:
    client, settings = agent_client
    response = client.post(
        "/projects/project-1/agent-connections",
        json={
            "name": "removed prompt agent",
            "agent_type": "prompt",
            "prompt_config": {
                "model": "removed",
                "endpoint": "https://llm.example.test/v1/chat/completions",
            },
            "release_identity": "removed-v1",
        },
        headers=headers(settings),
    )

    assert response.status_code == 404


def test_registers_endpoint_independent_agent_release(
    agent_client: tuple[TestClient, Settings],
) -> None:
    client, settings = agent_client
    response = client.post(
        "/projects/project-1/agent-releases",
        json={
            "label": "checkout candidate",
            "agent_type": "tool",
            "release_identity": "sha256:abc123",
            "source_revision": "abc123",
            "metadata": {"runtime": "python", "framework": "langgraph"},
        },
        headers=headers(settings),
    )

    assert response.status_code == 201
    release = response.json()
    assert release["project_id"] == "project-1"
    assert "agent_connection_id" not in release
    assert "endpoint_config" not in release
    assert release["release_identity"] == "sha256:abc123"
    assert release["source_revision"] == "abc123"
    assert release["metadata"]["framework"] == "langgraph"

    listed = client.get(
        "/projects/project-1/agent-releases",
        headers=headers(settings),
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [release["id"]]


@pytest.mark.parametrize("removed_agent_type", ["prompt", "demo", "mock"])
def test_endpoint_independent_release_rejects_removed_agent_types(
    agent_client: tuple[TestClient, Settings],
    removed_agent_type: str,
) -> None:
    client, settings = agent_client
    response = client.post(
        "/projects/project-1/agent-releases",
        json={
            "label": "removed release",
            "agent_type": removed_agent_type,
            "release_identity": "removed-v1",
        },
        headers=headers(settings),
    )

    assert response.status_code == 422
