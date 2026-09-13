import base64
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import agent_eval_api.provider_connections as provider_connections_module
from agent_eval_api import auth
from agent_eval_api.auth import get_db, issue_project_key
from agent_eval_api.db import (
    Base,
    EvaluatorVersionRecord,
    ProjectRecord,
    ProviderConnectionRecord,
)
from agent_eval_api.main import create_app
from agent_eval_api.provider_validation import (
    ProviderValidationError,
    ProviderValidationEvidence,
)
from agent_eval_api.settings import Settings

PLAINTEXT_CREDENTIAL = "deepseek-test-key-must-never-be-returned"
PRIVATE_FIELD_NAMES = {
    "api_key",
    "authorization",
    "credential_ciphertext",
    "credential_nonce",
}


@pytest.fixture
def provider_connection_client() -> Iterator[tuple[TestClient, Settings, Session]]:
    settings = Settings(
        database_url="sqlite:///:memory:",
        api_key_salt="test-salt",
        workspace_session_secret="test-session",
        credential_encryption_key=base64.b64encode(b"e" * 32).decode("ascii"),
        credential_encryption_key_id="test-key",
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
            ProviderConnectionRecord(
                id="provider-1",
                project_id="project-1",
                name="DeepSeek Judge",
                provider="openai_compatible",
                base_url="https://api.deepseek.com/v1",
                model="deepseek-chat",
                default_parameters={
                    "temperature": 0.0,
                    "max_tokens": 256,
                    "client": {"api_key": PLAINTEXT_CREDENTIAL},
                },
                credential_mask="ds-***-last4",
                credential_key_id="key-2026-09",
                credential_ciphertext=b"authenticated-ciphertext",
                credential_nonce=b"twelve-bytes",
                status="active",
                enabled=True,
                tested_at=datetime(2026, 9, 12, tzinfo=UTC),
            ),
            ProviderConnectionRecord(
                id="provider-2",
                project_id="project-2",
                name="Other Project Provider",
                provider="openai_compatible",
                base_url="https://provider.example.test/v1",
                model="private-model",
                default_parameters={},
                credential_mask="other-***",
                credential_key_id="key-other",
                credential_ciphertext=PLAINTEXT_CREDENTIAL.encode(),
                credential_nonce=b"other-nonce12",
                status="disabled",
                enabled=False,
            ),
        ]
    )
    session.commit()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[auth.get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, settings, session
    session.close()
    engine.dispose()


def _headers(settings: Settings, session: Session, project_id: str) -> dict[str, str]:
    raw_key, record = issue_project_key(project_id, settings)
    session.add(record)
    session.commit()
    return {"X-Project-Key": raw_key}


def _assert_secret_safe(document: object) -> None:
    serialized = str(document).lower()
    assert PLAINTEXT_CREDENTIAL.lower() not in serialized
    for field_name in PRIVATE_FIELD_NAMES:
        assert field_name not in serialized


def test_provider_connection_read_list_and_export_are_redacted(
    provider_connection_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = provider_connection_client
    project_headers = _headers(settings, session, "project-1")

    responses = [
        client.get("/projects/project-1/provider-connections", headers=project_headers),
        client.get(
            "/projects/project-1/provider-connections/provider-1",
            headers=project_headers,
        ),
        client.get(
            "/projects/project-1/provider-connections/export",
            headers=project_headers,
        ),
    ]

    for response in responses:
        assert response.status_code == 200, response.text
        _assert_secret_safe(response.json())

    connection = responses[1].json()
    assert connection["credential_mask"] == "ds-***-last4"
    assert connection["credential_key_id"] == "key-2026-09"
    assert connection["provider"] == "openai_compatible"
    assert connection["model"] == "deepseek-chat"
    assert connection["default_parameters"]["client"] == {}
    assert responses[2].json()["schema_version"] == 1


def test_provider_connections_are_project_scoped(
    provider_connection_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = provider_connection_client
    project_one_headers = _headers(settings, session, "project-1")
    project_two_headers = _headers(settings, session, "project-2")

    unauthorized = client.get(
        "/projects/project-2/provider-connections/provider-2",
        headers=project_one_headers,
    )
    assert unauthorized.status_code == 401

    hidden = client.get(
        "/projects/project-2/provider-connections/provider-1",
        headers=project_two_headers,
    )
    assert hidden.status_code == 404

    listed = client.get(
        "/projects/project-1/provider-connections",
        headers=project_one_headers,
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == ["provider-1"]
    _assert_secret_safe(listed.json())


def test_provider_connection_openapi_exposes_only_public_metadata(
    provider_connection_client: tuple[TestClient, Settings, Session],
) -> None:
    client, _, _ = provider_connection_client
    schema = client.get("/openapi.json").json()

    public_schema = schema["components"]["schemas"]["ProviderConnection"]
    assert set(public_schema["properties"]) == {
        "id",
        "project_id",
        "name",
        "provider",
        "base_url",
        "model",
        "default_parameters",
        "credential_mask",
        "credential_key_id",
        "status",
        "enabled",
        "created_at",
        "updated_at",
        "tested_at",
    }
    _assert_secret_safe(
        schema["paths"]["/projects/{project_id}/provider-connections"]["get"]
    )
    _assert_secret_safe(
        schema["paths"]["/projects/{project_id}/provider-connections/{connection_id}"]
    )
    _assert_secret_safe(
        schema["paths"]["/projects/{project_id}/provider-connections/export"]
    )


def test_provider_test_returns_evidence_without_saving_or_returning_key(
    provider_connection_client: tuple[TestClient, Settings, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, settings, session = provider_connection_client
    project_headers = _headers(settings, session, "project-1")
    credential = "test-endpoint-secret-never-returned"
    before = len(session.scalars(select(ProviderConnectionRecord)).all())
    monkeypatch.setattr(
        provider_connections_module,
        "validate_provider_connection",
        lambda **_: ProviderValidationEvidence(
            response_model="deepseek-chat",
            upstream_request_id="chatcmpl-real-provider-test",
            input_tokens=11,
            output_tokens=3,
            total_tokens=14,
        ),
    )

    response = client.post(
        "/projects/project-1/provider-connections/test",
        headers=project_headers,
        json={
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key": credential,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "provider": "openai_compatible",
        "configured_model": "deepseek-chat",
        "response_model": "deepseek-chat",
        "upstream_request_id": "chatcmpl-real-provider-test",
        "input_tokens": 11,
        "output_tokens": 3,
        "total_tokens": 14,
        "challenge_verified": True,
    }
    assert credential not in response.text
    assert len(session.scalars(select(ProviderConnectionRecord)).all()) == before


def test_create_validates_real_provider_before_encrypting_and_saving(
    provider_connection_client: tuple[TestClient, Settings, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, settings, session = provider_connection_client
    project_headers = _headers(settings, session, "project-1")
    credential = "deepseek-new-secret-never-returned"
    observed: dict[str, object] = {}

    def validate(**kwargs: object) -> None:
        observed.update(kwargs)

    monkeypatch.setattr(provider_connections_module, "validate_provider_connection", validate)
    response = client.post(
        "/projects/project-1/provider-connections",
        headers=project_headers,
        json={
            "name": "Validated DeepSeek",
            "provider": "openai_compatible",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key": credential,
            "default_parameters": {"temperature": 0.0},
            "timeout_seconds": 12,
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["status"] == "active"
    assert payload["enabled"] is True
    assert payload["tested_at"] is not None
    assert credential not in response.text
    assert observed == {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": credential,
        "model": "deepseek-chat",
        "timeout_seconds": 12.0,
    }

    record = session.get(ProviderConnectionRecord, payload["id"])
    assert record is not None
    assert record.credential_ciphertext != credential.encode()
    assert record.credential_nonce != b""
    assert record.credential_key_id == "test-key"


@pytest.mark.parametrize(
    "failure",
    [
        "provider rejected the credential",
        "provider endpoint or model was not found",
        "provider request timed out",
        "provider response failed the randomized challenge",
    ],
)
def test_failed_provider_validation_never_saves_connection(
    provider_connection_client: tuple[TestClient, Settings, Session],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    client, settings, session = provider_connection_client
    project_headers = _headers(settings, session, "project-1")
    credential = "failed-provider-secret-never-returned"

    def reject(**_: object) -> None:
        raise ProviderValidationError(failure)

    monkeypatch.setattr(provider_connections_module, "validate_provider_connection", reject)
    response = client.post(
        "/projects/project-1/provider-connections",
        headers=project_headers,
        json={
            "name": f"Failed {failure}",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key": credential,
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": failure}
    assert credential not in response.text
    assert session.scalar(
        select(ProviderConnectionRecord).where(
            ProviderConnectionRecord.name == f"Failed {failure}"
        )
    ) is None


def test_create_rejects_unsafe_url_and_credentials_in_default_parameters(
    provider_connection_client: tuple[TestClient, Settings, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, settings, session = provider_connection_client
    project_headers = _headers(settings, session, "project-1")
    monkeypatch.setattr(
        provider_connections_module,
        "validate_provider_connection",
        lambda **_: pytest.fail("invalid configuration must not contact provider"),
    )

    for payload in (
        {
            "name": "Unsafe URL",
            "base_url": "https://user:password@api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key": "test-secret",
        },
        {
            "name": "Nested Secret",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key": "test-secret",
            "default_parameters": {"client": {"api_key": "duplicate-secret"}},
        },
    ):
        response = client.post(
            "/projects/project-1/provider-connections",
            headers=project_headers,
            json=payload,
        )
        assert response.status_code == 422

    assert session.scalar(
        select(ProviderConnectionRecord).where(
            ProviderConnectionRecord.name.in_(["Unsafe URL", "Nested Secret"])
        )
    ) is None


def test_rotate_validates_then_replaces_encrypted_credential_without_echoing_it(
    provider_connection_client: tuple[TestClient, Settings, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, settings, session = provider_connection_client
    project_headers = _headers(settings, session, "project-1")
    record = session.get(ProviderConnectionRecord, "provider-1")
    assert record is not None
    original_ciphertext = record.credential_ciphertext
    original_nonce = record.credential_nonce
    replacement = "rotated-deepseek-secret-never-returned"
    observed: dict[str, object] = {}

    def validate(**kwargs: object) -> ProviderValidationEvidence:
        observed.update(kwargs)
        return ProviderValidationEvidence(
            response_model="deepseek-chat",
            upstream_request_id="chatcmpl-rotated",
            input_tokens=8,
            output_tokens=2,
            total_tokens=10,
        )

    monkeypatch.setattr(provider_connections_module, "validate_provider_connection", validate)
    response = client.post(
        "/projects/project-1/provider-connections/provider-1/rotate",
        headers=project_headers,
        json={"api_key": replacement, "timeout_seconds": 12},
    )

    assert response.status_code == 200, response.text
    assert replacement not in response.text
    _assert_secret_safe(response.json())
    assert observed == {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": replacement,
        "model": "deepseek-chat",
        "timeout_seconds": 12.0,
    }
    session.refresh(record)
    assert record.credential_ciphertext != original_ciphertext
    assert record.credential_nonce != original_nonce
    assert record.status == "active"
    assert record.enabled is True
    assert record.tested_at is not None


def test_failed_rotation_preserves_the_previous_credential(
    provider_connection_client: tuple[TestClient, Settings, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, settings, session = provider_connection_client
    project_headers = _headers(settings, session, "project-1")
    record = session.get(ProviderConnectionRecord, "provider-1")
    assert record is not None
    original = (
        record.credential_ciphertext,
        record.credential_nonce,
        record.credential_mask,
        record.credential_key_id,
    )
    monkeypatch.setattr(
        provider_connections_module,
        "validate_provider_connection",
        lambda **_: (_ for _ in ()).throw(
            ProviderValidationError("provider rejected rotated credential")
        ),
    )

    response = client.post(
        "/projects/project-1/provider-connections/provider-1/rotate",
        headers=project_headers,
        json={"api_key": "invalid-rotated-secret"},
    )

    assert response.status_code == 422
    assert "invalid-rotated-secret" not in response.text
    session.refresh(record)
    assert (
        record.credential_ciphertext,
        record.credential_nonce,
        record.credential_mask,
        record.credential_key_id,
    ) == original


def test_provider_can_be_disabled_reenabled_and_deleted_when_unreferenced(
    provider_connection_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = provider_connection_client
    project_headers = _headers(settings, session, "project-1")

    disabled = client.patch(
        "/projects/project-1/provider-connections/provider-1/enabled?enabled=false",
        headers=project_headers,
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    assert disabled.json()["status"] == "disabled"
    _assert_secret_safe(disabled.json())

    enabled = client.patch(
        "/projects/project-1/provider-connections/provider-1/enabled?enabled=true",
        headers=project_headers,
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True
    assert enabled.json()["status"] == "active"

    deleted = client.delete(
        "/projects/project-1/provider-connections/provider-1",
        headers=project_headers,
    )
    assert deleted.status_code == 204, deleted.text
    assert session.get(ProviderConnectionRecord, "provider-1") is None


def test_provider_delete_is_rejected_when_an_immutable_judge_references_it(
    provider_connection_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = provider_connection_client
    project_headers = _headers(settings, session, "project-1")
    session.add(
        EvaluatorVersionRecord(
            id="judge-using-provider-1",
            project_id="project-1",
            name="quality_judge",
            version="1.0.0",
            evaluator_type="llm_judge",
            requires=[],
            supported_agent_types=["tool"],
            direction="higher_is_better",
            provider_connection_id="provider-1",
            judge_model="deepseek-chat",
            config={},
        )
    )
    session.commit()

    response = client.delete(
        "/projects/project-1/provider-connections/provider-1",
        headers=project_headers,
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "provider connection is referenced by an immutable Judge evaluator"
    }
    assert session.get(ProviderConnectionRecord, "provider-1") is not None
