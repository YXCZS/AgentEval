from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from agent_eval_api import auth
from agent_eval_api.auth import get_db, issue_dev_session
from agent_eval_api.db import (
    Base,
    EvaluatorVersionRecord,
    ProjectRecord,
    ProviderConnectionRecord,
)
from agent_eval_api.main import create_app
from agent_eval_api.settings import Settings


@pytest.fixture
def evaluator_client() -> Iterator[tuple[TestClient, Settings, Session]]:
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


def headers(settings: Settings, project_id: str = "project-1") -> dict[str, str]:
    return {"X-Workspace-Session": issue_dev_session(project_id, settings)}


def evaluator_payload() -> dict[str, object]:
    return {
        "name": "task_success",
        "version": "1.0.0",
        "evaluator_type": "deterministic",
        "requires": ["expected_state"],
        "supported_agent_types": ["tool"],
        "score_min": 0,
        "score_max": 1,
        "direction": "higher_is_better",
        "default_threshold": 1,
        "config": {"comparison": "subset"},
    }


def add_provider(
    session: Session,
    *,
    provider_id: str = "provider-1",
    project_id: str = "project-1",
    status: str = "active",
    enabled: bool = True,
) -> ProviderConnectionRecord:
    provider = ProviderConnectionRecord(
        id=provider_id,
        project_id=project_id,
        name=provider_id,
        provider="openai_compatible",
        base_url="https://provider.example.test/v1",
        model="provider-default-model",
        default_parameters={"frequency_penalty": 0},
        credential_mask="sk-...test",
        credential_key_id="test-key",
        credential_ciphertext=b"ciphertext",
        credential_nonce=b"012345678901",
        status=status,
        enabled=enabled,
    )
    session.add(provider)
    session.commit()
    return provider


def managed_judge_payload(provider_id: str = "provider-1") -> dict[str, object]:
    return {
        "name": "answer_quality",
        "version": "1.0.0",
        "evaluator_type": "llm_judge",
        "requires": ["expected_output"],
        "supported_agent_types": ["rag", "tool"],
        "score_min": 0,
        "score_max": 1,
        "direction": "higher_is_better",
        "default_threshold": 0.8,
        "rubric": "Judge factual correctness against the expected output.",
        "provider_connection_id": provider_id,
        "judge_model": "deepseek-chat",
        "prompt_template": (
            "Input: {{ input }}\nExpected: {{ expected_output }}\nActual: {{ actual_output }}"
        ),
        "output_schema": {
            "type": "object",
            "required": ["score", "explanation"],
            "properties": {
                "score": {"type": "number"},
                "explanation": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "sampling_parameters": {"temperature": 0, "top_p": 1, "max_tokens": 500},
        "config": {"max_retries": 2},
    }


def test_register_and_disable_project_scoped_evaluator_version(
    evaluator_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = evaluator_client
    created = client.post(
        "/projects/project-1/evaluators",
        json=evaluator_payload(),
        headers=headers(settings),
    )

    assert created.status_code == 201
    evaluator = created.json()
    assert evaluator["name"] == "task_success"
    assert evaluator["requires"] == ["expected_state"]
    assert evaluator["enabled"] is True
    record = session.get(EvaluatorVersionRecord, evaluator["id"])
    assert record is not None
    assert record.project_id == "project-1"

    disabled = client.patch(
        f"/projects/project-1/evaluators/{evaluator['id']}/enabled?enabled=false",
        headers=headers(settings),
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert client.get(
        "/projects/project-1/evaluators?enabled=true", headers=headers(settings)
    ).json() == []


def test_evaluator_registration_validates_contract_and_project_boundaries(
    evaluator_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = evaluator_client
    invalid = client.post(
        "/projects/project-1/evaluators",
        json={**evaluator_payload(), "score_min": 1, "score_max": 1},
        headers=headers(settings),
    )
    assert invalid.status_code == 422

    created = client.post(
        "/projects/project-1/evaluators",
        json=evaluator_payload(),
        headers=headers(settings),
    )
    assert created.status_code == 201
    duplicate = client.post(
        "/projects/project-1/evaluators",
        json=evaluator_payload(),
        headers=headers(settings),
    )
    assert duplicate.status_code == 409

    isolated = client.get(
        f"/projects/project-2/evaluators/{created.json()['id']}",
        headers=headers(settings, "project-2"),
    )
    assert isolated.status_code == 404


def test_registers_complete_versioned_managed_judge_configuration(
    evaluator_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = evaluator_client
    add_provider(session)

    response = client.post(
        "/projects/project-1/evaluators",
        json=managed_judge_payload(),
        headers=headers(settings),
    )

    assert response.status_code == 201
    evaluator = response.json()
    assert evaluator["provider_connection_id"] == "provider-1"
    assert evaluator["evaluator_connection_id"] is None
    assert evaluator["judge_model"] == "deepseek-chat"
    assert evaluator["prompt_template"].startswith("Input:")
    assert evaluator["output_schema"]["required"] == ["score", "explanation"]
    assert evaluator["sampling_parameters"] == {
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": 500,
        "seed": None,
    }
    record = session.get(EvaluatorVersionRecord, evaluator["id"])
    assert record is not None
    assert record.provider_connection_id == "provider-1"


def test_managed_judge_rejects_ambiguous_incomplete_or_invalid_configuration(
    evaluator_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = evaluator_client
    add_provider(session)

    incomplete = managed_judge_payload()
    incomplete.pop("judge_model")
    response = client.post(
        "/projects/project-1/evaluators",
        json=incomplete,
        headers=headers(settings),
    )
    assert response.status_code == 422
    assert "judge_model" in response.text

    invalid_schema = managed_judge_payload()
    invalid_schema["output_schema"] = {"type": "not-a-json-schema-type"}
    response = client.post(
        "/projects/project-1/evaluators",
        json=invalid_schema,
        headers=headers(settings),
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "output_schema is not a valid JSON Schema"

    ambiguous = managed_judge_payload()
    ambiguous["evaluator_connection_id"] = "external-1"
    response = client.post(
        "/projects/project-1/evaluators",
        json=ambiguous,
        headers=headers(settings),
    )
    assert response.status_code == 422
    assert "exactly one" in response.json()["detail"]


def test_managed_judge_rejects_cross_project_or_inactive_provider(
    evaluator_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = evaluator_client
    add_provider(session, provider_id="other-project", project_id="project-2")
    add_provider(session, provider_id="disabled", status="disabled", enabled=False)

    for provider_id in ("other-project", "disabled", "missing"):
        response = client.post(
            "/projects/project-1/evaluators",
            json=managed_judge_payload(provider_id),
            headers=headers(settings),
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "active provider connection not found"


def test_future_adapter_capabilities_are_read_only_and_explicit(
    evaluator_client: tuple[TestClient, Settings, Session],
) -> None:
    client, _, _ = evaluator_client
    response = client.get("/adapter-capabilities")

    assert response.status_code == 200
    capabilities = {item["adapter_id"]: item for item in response.json()}
    assert capabilities["giskard-safety"]["lifecycle"] == "planned"
    assert capabilities["webarena"]["execution_mode"] == "external_environment"
    assert capabilities["webarena"]["requires_external_environment"] is True
