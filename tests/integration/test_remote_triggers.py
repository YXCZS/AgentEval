import base64
from collections.abc import Iterator
from unittest.mock import ANY

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from agent_eval_api import auth
from agent_eval_api.auth import get_db, issue_dev_session
from agent_eval_api.db import (
    Base,
    DatasetRecord,
    ProjectRecord,
    RemoteTriggerDeliveryRecord,
    RemoteTriggerRecord,
)
from agent_eval_api.main import create_app
from agent_eval_api.remote_trigger_protocol import (
    RemoteTriggerDelivery,
    verify_remote_trigger_delivery,
)
from agent_eval_api.remote_triggers import RemoteTriggerDeliveryError
from agent_eval_api.settings import Settings


def _headers(settings: Settings, project_id: str = "project-1") -> dict[str, str]:
    return {"X-Workspace-Session": issue_dev_session(project_id, settings)}


@pytest.fixture
def trigger_client() -> Iterator[tuple[TestClient, Settings, Session]]:
    settings = Settings(
        database_url="sqlite:///:memory:",
        api_key_salt="test-salt",
        workspace_session_secret="test-session",
        credential_encryption_key=base64.b64encode(b"t" * 32).decode("ascii"),
        credential_encryption_key_id="trigger-key",
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
    dataset = DatasetRecord(
        id="dataset-1",
        project_id="project-1",
        name="support cases",
        tags=[],
    )
    session.add(dataset)
    session.commit()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[auth.get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, settings, session
    session.close()


def test_remote_trigger_secret_is_returned_once_and_never_on_read(
    trigger_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = trigger_client
    path = "/projects/project-1/datasets/dataset-1/remote-trigger"
    created = client.post(
        path,
        json={"trigger_url": "https://runner.example.test/agent-eval", "enabled": True},
        headers=_headers(settings),
    )
    assert created.status_code == 201
    body = created.json()
    signing_secret = body["signing_secret"]
    assert signing_secret.startswith("aet_")
    assert body["signature_header"] == "X-Agent-Eval-Trigger-Signature"

    read = client.get(path, headers=_headers(settings))
    assert read.status_code == 200
    read_body = read.json()
    assert "signing_secret" not in read_body
    assert signing_secret not in read.text
    assert read_body["secret_mask"] != signing_secret

    stored = session.get(RemoteTriggerRecord, body["id"])
    assert stored is not None
    assert signing_secret not in stored.secret_ciphertext.decode("latin1", errors="ignore")
    assert stored.secret_key_id == "trigger-key"


def test_remote_trigger_can_be_disabled_or_repointed_but_cannot_be_recreated(
    trigger_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = trigger_client
    path = "/projects/project-1/datasets/dataset-1/remote-trigger"
    created = client.post(
        path,
        json={"trigger_url": "https://runner.example.test/first"},
        headers=_headers(settings),
    )
    assert created.status_code == 201

    duplicate = client.post(
        path,
        json={"trigger_url": "https://runner.example.test/second"},
        headers=_headers(settings),
    )
    assert duplicate.status_code == 409

    updated = client.patch(
        path,
        json={"trigger_url": "https://runner.example.test/second", "enabled": False},
        headers=_headers(settings),
    )
    assert updated.status_code == 200
    assert updated.json()["trigger_url"] == "https://runner.example.test/second"
    assert updated.json()["enabled"] is False
    assert "signing_secret" not in updated.json()


def test_remote_trigger_rejects_urls_with_embedded_credentials_or_query(
    trigger_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = trigger_client
    path = "/projects/project-1/datasets/dataset-1/remote-trigger"
    for url in (
        "https://user:password@runner.example.test/hook",
        "https://runner.example.test/hook?secret=leak",
        "https://runner.example.test/hook#secret",
    ):
        response = client.post(path, json={"trigger_url": url}, headers=_headers(settings))
        assert response.status_code == 422


def test_remote_trigger_experiment_sends_signed_non_secret_run_payload(
    trigger_client: tuple[TestClient, Settings, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, settings, _ = trigger_client
    dataset = client.post(
        "/projects/project-1/datasets",
        json={
            "name": "remote cases",
            "cases": [{"id": "case-1", "input": {"question": "Where is order 42?"}}],
        },
        headers=_headers(settings),
    )
    assert dataset.status_code == 201
    dataset_body = dataset.json()
    release = client.post(
        "/projects/project-1/agent-releases",
        json={
            "label": "remote release",
            "agent_type": "tool",
            "release_identity": "git-sha-remote",
            "source_revision": "git-sha-remote",
        },
        headers=_headers(settings),
    )
    assert release.status_code == 201
    evaluator = client.post(
        "/projects/project-1/evaluators",
        json={
            "name": "remote task success",
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
    assert evaluator.status_code == 201

    trigger_path = (
        f"/projects/project-1/datasets/{dataset_body['id']}/remote-trigger"
    )
    trigger = client.post(
        trigger_path,
        json={"trigger_url": "https://runner.example.test/agent-eval"},
        headers=_headers(settings),
    )
    assert trigger.status_code == 201
    trigger_body = trigger.json()
    captured: list[RemoteTriggerDelivery] = []

    def capture_delivery(
        trigger_record: RemoteTriggerRecord, delivery: RemoteTriggerDelivery
    ) -> int:
        assert trigger_record.dataset_id == dataset_body["id"]
        captured.append(delivery)
        return 200

    monkeypatch.setattr(
        "agent_eval_api.remote_triggers.dispatch_experiment_trigger", capture_delivery
    )
    created = client.post(
        "/projects/project-1/experiments",
        json={
            "name": "remote trigger experiment",
            "agent_version_id": release.json()["id"],
            "dataset_version_id": dataset_body["current_version_id"],
            "evaluator_version_ids": [evaluator.json()["id"]],
            "execution_mode": "remote_trigger",
        },
        headers=_headers(settings),
    )
    assert created.status_code == 201
    assert created.json()["status"] == "queued"
    assert len(captured) == 1
    delivery = captured[0]
    payload = verify_remote_trigger_delivery(
        delivery.headers,
        delivery.body,
        signing_secret=trigger_body["signing_secret"],
    )
    assert payload["experiment"]["id"] == created.json()["id"]
    assert payload["dataset"] == {
        "id": dataset_body["id"],
        "version_id": dataset_body["current_version_id"],
        "version": 1,
    }
    assert payload["callback"]["manifest_url"].endswith(
        f"/projects/project-1/experiments/{created.json()['id']}/manifest"
    )
    assert trigger_body["signing_secret"] not in delivery.body.decode("utf-8")
    assert "X-Project-Key" not in delivery.headers

    deliveries = client.get(f"{trigger_path}/deliveries", headers=_headers(settings))
    assert deliveries.status_code == 200
    assert deliveries.json() == [
        {
            "id": ANY,
            "trigger_id": trigger_body["id"],
            "experiment_id": created.json()["id"],
            "delivery_id": delivery.delivery_id,
            "status": "accepted",
            "attempt_count": 1,
            "last_http_status": 200,
            "last_error_type": None,
            "last_error_message": None,
            "accepted_at": ANY,
            "created_at": ANY,
            "updated_at": ANY,
        }
    ]


def test_remote_trigger_retries_delivery_without_completing_experiment(
    trigger_client: tuple[TestClient, Settings, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, settings, session = trigger_client
    dataset = client.post(
        "/projects/project-1/datasets",
        json={
            "name": "retry cases",
            "cases": [{"id": "case-1", "input": {"question": "Where is order 42?"}}],
        },
        headers=_headers(settings),
    )
    assert dataset.status_code == 201
    release = client.post(
        "/projects/project-1/agent-releases",
        json={
            "label": "retry release",
            "agent_type": "tool",
            "release_identity": "git-sha-retry",
        },
        headers=_headers(settings),
    )
    assert release.status_code == 201
    evaluator = client.post(
        "/projects/project-1/evaluators",
        json={
            "name": "retry task success",
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
    assert evaluator.status_code == 201
    trigger_path = (
        f"/projects/project-1/datasets/{dataset.json()['id']}/remote-trigger"
    )
    assert client.post(
        trigger_path,
        json={"trigger_url": "https://runner.example.test/retry"},
        headers=_headers(settings),
    ).status_code == 201

    calls = 0

    def fail_once(*_: object) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RemoteTriggerDeliveryError("timeout", retryable=True)
        return 202

    monkeypatch.setattr(
        "agent_eval_api.remote_triggers.dispatch_experiment_trigger", fail_once
    )
    created = client.post(
        "/projects/project-1/experiments",
        json={
            "name": "retry trigger experiment",
            "agent_version_id": release.json()["id"],
            "dataset_version_id": dataset.json()["current_version_id"],
            "evaluator_version_ids": [evaluator.json()["id"]],
            "execution_mode": "remote_trigger",
        },
        headers=_headers(settings),
    )
    assert created.status_code == 201
    assert created.json()["status"] == "queued"
    assert calls == 2

    deliveries = client.get(f"{trigger_path}/deliveries", headers=_headers(settings))
    assert deliveries.status_code == 200
    delivery = deliveries.json()[0]
    assert delivery["status"] == "accepted"
    assert delivery["attempt_count"] == 2
    assert delivery["last_http_status"] == 202
    assert delivery["accepted_at"] is not None

    stored = session.get(RemoteTriggerDeliveryRecord, delivery["id"])
    assert stored is not None
    assert stored.experiment_id == created.json()["id"]


def test_remote_trigger_rejection_is_observable_without_retrying(
    trigger_client: tuple[TestClient, Settings, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, settings, _ = trigger_client
    dataset = client.post(
        "/projects/project-1/datasets",
        json={"name": "rejected cases", "cases": [{"id": "case-1", "input": {}}]},
        headers=_headers(settings),
    )
    release = client.post(
        "/projects/project-1/agent-releases",
        json={"label": "rejected release", "agent_type": "tool", "release_identity": "r1"},
        headers=_headers(settings),
    )
    evaluator = client.post(
        "/projects/project-1/evaluators",
        json={
            "name": "rejected evaluator",
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
    trigger_path = f"/projects/project-1/datasets/{dataset.json()['id']}/remote-trigger"
    client.post(
        trigger_path,
        json={"trigger_url": "https://runner.example.test/rejected"},
        headers=_headers(settings),
    )

    calls = 0

    def reject(*_: object) -> int:
        nonlocal calls
        calls += 1
        raise RemoteTriggerDeliveryError("http_401", retryable=False, http_status=401)

    monkeypatch.setattr("agent_eval_api.remote_triggers.dispatch_experiment_trigger", reject)
    created = client.post(
        "/projects/project-1/experiments",
        json={
            "name": "rejected trigger experiment",
            "agent_version_id": release.json()["id"],
            "dataset_version_id": dataset.json()["current_version_id"],
            "evaluator_version_ids": [evaluator.json()["id"]],
            "execution_mode": "remote_trigger",
        },
        headers=_headers(settings),
    )
    assert created.status_code == 201
    assert calls == 1
    delivery = client.get(f"{trigger_path}/deliveries", headers=_headers(settings)).json()[0]
    assert delivery["status"] == "rejected"
    assert delivery["attempt_count"] == 1
    assert delivery["last_http_status"] == 401
    assert delivery["last_error_message"] == "remote runner did not accept this delivery"
