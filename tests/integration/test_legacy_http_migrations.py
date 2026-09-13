import base64
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from agent_eval_api import auth
from agent_eval_api.auth import get_db, issue_dev_session
from agent_eval_api.db import (
    AgentRecord,
    AgentVersionRecord,
    Base,
    DatasetRecord,
    ProjectRecord,
    RemoteTriggerRecord,
)
from agent_eval_api.main import create_app
from agent_eval_api.settings import Settings, get_settings


def _headers(settings: Settings) -> dict[str, str]:
    return {"X-Workspace-Session": issue_dev_session("project-1", settings)}


@pytest.fixture
def migration_client() -> Iterator[tuple[TestClient, Settings, Session, str, str]]:
    settings = Settings(
        database_url="sqlite:///:memory:",
        api_key_salt="test-salt",
        workspace_session_secret="test-session",
        credential_encryption_key=base64.b64encode(b"m" * 32).decode("ascii"),
        credential_encryption_key_id="migration-key",
    )
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = ProjectRecord(id="project-1", name="one")
    dataset = DatasetRecord(id="dataset-1", project_id=project.id, name="legacy cases", tags=[])
    legacy_agent = AgentRecord(
        id="legacy-agent-1",
        project_id=project.id,
        name="retired HTTP Agent",
        agent_type="tool",
    )
    legacy_release = AgentVersionRecord(
        id="legacy-release-1",
        project_id=project.id,
        agent_id=legacy_agent.id,
        version=1,
        label="legacy release",
        agent_type="tool",
        release_identity="git:legacy-abc",
        source_revision="legacy-abc",
        metadata_json={"untrusted": "not copied"},
        endpoint_config={
            "url": "https://agent.example.test/run",
            "auth_ref": "legacy-agent-credential",
        },
    )
    session.add_all([project, dataset, legacy_agent, legacy_release])
    session.commit()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[auth.get_settings] = lambda: settings
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, settings, session, legacy_release.id, dataset.id
    session.close()
    engine.dispose()


def _migrate(
    client: TestClient,
    settings: Settings,
    legacy_release_id: str,
    dataset_id: str,
    **overrides: object,
):
    payload: dict[str, object] = {
        "legacy_agent_version_id": legacy_release_id,
        "dataset_id": dataset_id,
        "target_mode": "remote_upload",
    }
    payload.update(overrides)
    return client.post(
        "/projects/project-1/legacy-http-agent-migrations",
        json=payload,
        headers=_headers(settings),
    )


def test_legacy_http_release_migrates_to_endpoint_independent_remote_upload(
    migration_client: tuple[TestClient, Settings, Session, str, str],
) -> None:
    client, settings, _, legacy_release_id, dataset_id = migration_client

    response = _migrate(client, settings, legacy_release_id, dataset_id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["trigger"] is None
    release = body["release"]
    assert release["release_identity"] == "git:legacy-abc"
    assert release["source_revision"] == "legacy-abc"
    assert release["metadata"] == {
        "migration": {
            "source_legacy_agent_version_id": legacy_release_id,
            "target_mode": "remote_upload",
        }
    }
    assert "endpoint_config" not in release
    assert "agent_connection_id" not in release
    assert "legacy-agent-credential" not in response.text

    listed = client.get("/projects/project-1/agent-releases", headers=_headers(settings))
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [release["id"]]

    evaluator = client.post(
        "/projects/project-1/evaluators",
        json={
            "name": "migration task success",
            "version": "1.0.0",
            "evaluator_type": "deterministic",
            "requires": [],
            "supported_agent_types": ["tool"],
            "score_min": 0,
            "score_max": 1,
            "direction": "higher_is_better",
            "default_threshold": 1,
        },
        headers=_headers(settings),
    )
    dataset = client.post(
        "/projects/project-1/datasets",
        json={"name": "migration experiment", "cases": [{"id": "case-1", "input": {}}]},
        headers=_headers(settings),
    )
    assert evaluator.status_code == 201
    assert dataset.status_code == 201
    experiment = client.post(
        "/projects/project-1/experiments",
        json={
            "name": "migrated release remote upload",
            "agent_version_id": release["id"],
            "dataset_version_id": dataset.json()["current_version_id"],
            "evaluator_version_ids": [evaluator.json()["id"]],
            "execution_mode": "remote_upload",
        },
        headers=_headers(settings),
    )
    assert experiment.status_code == 201
    assert "endpoint_config" not in experiment.json()["configuration_snapshot"]["agent_version"]


def test_legacy_http_release_migrates_to_remote_trigger_with_one_time_secret(
    migration_client: tuple[TestClient, Settings, Session, str, str],
) -> None:
    client, settings, session, legacy_release_id, dataset_id = migration_client

    response = _migrate(
        client,
        settings,
        legacy_release_id,
        dataset_id,
        target_mode="remote_trigger",
        trigger_url="https://runner.example.test/agent-eval",
    )

    assert response.status_code == 201, response.text
    trigger = response.json()["trigger"]
    assert trigger["trigger_url"] == "https://runner.example.test/agent-eval"
    assert trigger["signing_secret"].startswith("aet_")
    assert "legacy-agent-credential" not in response.text
    stored_trigger = session.get(RemoteTriggerRecord, trigger["id"])
    assert stored_trigger is not None
    assert trigger["signing_secret"] not in stored_trigger.secret_ciphertext.decode(
        "latin1", errors="ignore"
    )

    read = client.get(
        f"/projects/project-1/datasets/{dataset_id}/remote-trigger",
        headers=_headers(settings),
    )
    assert read.status_code == 200
    assert "signing_secret" not in read.json()


@pytest.mark.parametrize(
    "payload",
    [
        {"target_mode": "remote_trigger"},
        {
            "target_mode": "remote_trigger",
            "trigger_url": "https://runner.example.test/hook?token=private",
        },
        {
            "target_mode": "remote_upload",
            "trigger_url": "https://runner.example.test/hook",
        },
    ],
)
def test_legacy_migration_validates_explicit_safe_target_configuration(
    migration_client: tuple[TestClient, Settings, Session, str, str],
    payload: dict[str, str],
) -> None:
    client, settings, _, legacy_release_id, dataset_id = migration_client

    response = _migrate(client, settings, legacy_release_id, dataset_id, **payload)

    assert response.status_code == 422
    assert "private" not in response.text


def test_existing_trigger_rejects_migration_without_creating_a_release(
    migration_client: tuple[TestClient, Settings, Session, str, str],
) -> None:
    client, settings, session, legacy_release_id, dataset_id = migration_client
    trigger_path = f"/projects/project-1/datasets/{dataset_id}/remote-trigger"
    assert client.post(
        trigger_path,
        json={"trigger_url": "https://runner.example.test/existing"},
        headers=_headers(settings),
    ).status_code == 201

    response = _migrate(
        client,
        settings,
        legacy_release_id,
        dataset_id,
        target_mode="remote_trigger",
        trigger_url="https://runner.example.test/new",
    )

    assert response.status_code == 409
    releases = session.scalars(
        select(AgentVersionRecord).where(AgentVersionRecord.agent_id.is_(None))
    ).all()
    assert releases == []


def test_legacy_http_release_can_never_create_a_new_experiment(
    migration_client: tuple[TestClient, Settings, Session, str, str],
) -> None:
    client, settings, _, legacy_release_id, _ = migration_client
    dataset = client.post(
        "/projects/project-1/datasets",
        json={"name": "experiment cases", "cases": [{"id": "case-1", "input": {}}]},
        headers=_headers(settings),
    )
    evaluator = client.post(
        "/projects/project-1/evaluators",
        json={
            "name": "task success",
            "version": "1.0.0",
            "evaluator_type": "deterministic",
            "requires": [],
            "supported_agent_types": ["tool"],
            "score_min": 0,
            "score_max": 1,
            "direction": "higher_is_better",
            "default_threshold": 1,
        },
        headers=_headers(settings),
    )
    assert dataset.status_code == 201
    assert evaluator.status_code == 201

    created = client.post(
        "/projects/project-1/experiments",
        json={
            "name": "must reject old runtime",
            "agent_version_id": legacy_release_id,
            "dataset_version_id": dataset.json()["current_version_id"],
            "evaluator_version_ids": [evaluator.json()["id"]],
            "execution_mode": "remote_upload",
        },
        headers=_headers(settings),
    )

    assert created.status_code == 422
    assert "legacy HTTP Agent releases cannot create experiments" in created.json()["detail"]


def test_openapi_has_no_per_case_http_agent_contract(
    migration_client: tuple[TestClient, Settings, Session, str, str],
) -> None:
    client, _, _, _, _ = migration_client

    schema = client.get("/openapi.json").json()

    assert not any("agent-connections" in path for path in schema["paths"])
    assert "EndpointConfig" not in schema["components"]["schemas"]
    assert "ExternalAgentRequest" not in schema["components"]["schemas"]
    assert "ExternalAgentResponse" not in schema["components"]["schemas"]
    assert not any(path.endswith("/run") for path in schema["paths"])
