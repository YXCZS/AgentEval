import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APITimeoutError
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from agent_eval_api import auth
from agent_eval_api.auth import get_db, issue_dev_session
from agent_eval_api.credential_encryption import CredentialCipher
from agent_eval_api.db import (
    Base,
    EvaluationRunRecord,
    ProjectRecord,
    ProviderConnectionRecord,
    ScoreRecord,
    TraceSpanRecord,
)
from agent_eval_api.evaluation import ManagedJudgeRetryableError
from agent_eval_api.main import create_app
from agent_eval_api.settings import Settings
from agent_eval_worker.managed_judge import execute_managed_judge, fail_managed_judge

TEST_PROVIDER_KEY = "sk-test-managed-worker-secret"


def headers(settings: Settings, project_id: str = "project-1") -> dict[str, str]:
    return {"X-Workspace-Session": issue_dev_session(project_id, settings)}


def create_standalone_tool_release(client: TestClient, settings: Settings) -> str:
    response = client.post(
        "/projects/project-1/agent-releases",
        json={
            "label": "Managed Judge test release",
            "agent_type": "tool",
            "release_identity": "test-managed-judge-release",
            "source_revision": "test",
            "metadata": {"runtime": "test"},
        },
        headers=headers(settings),
    )
    assert response.status_code == 201
    return response.json()["id"]


def create_evaluator(
    client: TestClient,
    settings: Settings,
    *,
    name: str,
    requires: list[str],
    evaluator_type: str,
    provider_connection_id: str,
    judge_model: str,
    prompt_template: str,
    output_schema: dict[str, Any],
    sampling_parameters: dict[str, Any],
    config: dict[str, Any],
    rubric: str,
    threshold: float,
) -> str:
    response = client.post(
        "/projects/project-1/evaluators",
        json={
            "name": name,
            "version": "1.0.0",
            "evaluator_type": evaluator_type,
            "requires": requires,
            "supported_agent_types": ["tool"],
            "score_min": 0,
            "score_max": 1,
            "direction": "higher_is_better",
            "default_threshold": threshold,
            "provider_connection_id": provider_connection_id,
            "judge_model": judge_model,
            "prompt_template": prompt_template,
            "output_schema": output_schema,
            "sampling_parameters": sampling_parameters,
            "config": config,
            "rubric": rubric,
        },
        headers=headers(settings),
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.fixture
def managed_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, Settings, Session]]:
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


class ProviderCompletions:
    def __init__(self, result: object) -> None:
        self.result = result
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> object:
        self.requests.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class ProviderClient:
    def __init__(self, result: object) -> None:
        self.completions = ProviderCompletions(result)
        self.chat = SimpleNamespace(completions=self.completions)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _worker_settings(settings: Settings) -> Settings:
    encoded_key = base64.b64encode(b"0" * 32).decode("ascii")
    return Settings(
        database_url=settings.database_url,
        redis_url=settings.redis_url,
        api_key_salt=settings.api_key_salt,
        workspace_session_secret=settings.workspace_session_secret,
        credential_encryption_key=SecretStr(encoded_key),
        credential_encryption_key_id="test-managed-key",
    )


def _add_encrypted_provider(
    session: Session,
    settings: Settings,
) -> ProviderConnectionRecord:
    connection_id = "managed-worker-provider"
    encrypted = CredentialCipher.from_settings(settings).encrypt(
        TEST_PROVIDER_KEY,
        project_id="project-1",
        connection_id=connection_id,
    )
    provider = ProviderConnectionRecord(
        id=connection_id,
        project_id="project-1",
        name="Worker Judge",
        provider="openai_compatible",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        default_parameters={
            "pricing": {
                "input_per_million_tokens": 2,
                "cached_input_per_million_tokens": 0.5,
                "output_per_million_tokens": 8,
                "currency": "USD",
            }
        },
        credential_mask=encrypted.mask,
        credential_key_id=encrypted.key_id,
        credential_ciphertext=encrypted.ciphertext,
        credential_nonce=encrypted.nonce,
        status="active",
        enabled=True,
    )
    session.add(provider)
    session.commit()
    return provider


def _prepare_pending_score(
    managed_client: tuple[TestClient, Settings, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, Settings, Session, str, str]:
    client, api_settings, session = managed_client
    worker_settings = _worker_settings(api_settings)
    provider = _add_encrypted_provider(session, worker_settings)
    release_id = create_standalone_tool_release(client, api_settings)
    dataset = client.post(
        "/projects/project-1/datasets",
        json={
            "name": "Managed Judge worker Dataset",
            "cases": [
                {
                    "id": "order-42",
                    "input": {"question": "Where is order 42?"},
                    "expected_output": "Order 42 has shipped.",
                }
            ],
        },
        headers=headers(api_settings),
    )
    assert dataset.status_code == 201
    evaluator_id = create_evaluator(
        client,
        api_settings,
        name="managed_answer_quality",
        requires=[],
        evaluator_type="llm_judge",
        provider_connection_id=provider.id,
        judge_model="deepseek-chat",
        prompt_template=(
            "Input={{ input | json }}\nExpected={{ expected_output | json }}\n"
            "Actual={{ actual_output | json }}\nRubric={{ rubric }}"
        ),
        output_schema={
            "type": "object",
            "required": ["score", "explanation"],
            "properties": {
                "score": {"type": "number"},
                "explanation": {"type": "string"},
            },
            "additionalProperties": False,
        },
        sampling_parameters={"temperature": 0, "max_tokens": 200},
        config={
            "timeout_seconds": 12,
            "max_retries": 2,
            "retry_backoff_seconds": 0.25,
        },
        rubric="Score factual correctness from 0 to 1.",
        threshold=0.8,
    )
    experiment = client.post(
        "/projects/project-1/experiments",
        json={
            "name": "Managed Judge worker run",
            "agent_version_id": release_id,
            "dataset_version_id": dataset.json()["current_version_id"],
            "evaluator_version_ids": [evaluator_id],
        },
        headers=headers(api_settings),
    )
    assert experiment.status_code == 201
    run_id = experiment.json()["id"]
    item = client.post(
        f"/projects/project-1/experiments/{run_id}/items/start",
        json={"case_id": "order-42", "external_run_id": "managed-worker-run"},
        headers=headers(api_settings),
    )
    assert item.status_code == 201
    item_id = item.json()["id"]
    public_trace_id = "abcdef0123456789abcdef0123456789"
    completed = client.post(
        f"/projects/project-1/experiments/{run_id}/items/{item_id}/complete",
        json={
            "output": {"answer": "Order 42 has shipped."},
            "trace_id": public_trace_id,
        },
        headers=headers(api_settings),
    )
    assert completed.status_code == 200
    now = datetime.now(UTC).isoformat()
    ingested = client.post(
        "/projects/project-1/traces/ingest",
        json={
            "source": "sdk",
            "trace": {
                "trace_id": public_trace_id,
                "run_id": run_id,
                "case_id": "order-42",
                "status": "completed",
                "source": "sdk",
                "spans": [
                    {
                        "span_id": "abcdef0123456789",
                        "trace_id": public_trace_id,
                        "kind": "agent",
                        "name": "real user Agent task",
                        "status": "completed",
                        "started_at": now,
                        "ended_at": now,
                        "attributes": {
                            "agent_eval.project.id": "project-1",
                            "agent_eval.experiment.id": run_id,
                            "agent_eval.experiment.item.id": item_id,
                            "agent_eval.dataset.version.id": dataset.json()[
                                "current_version_id"
                            ],
                            "agent_eval.case.id": "order-42",
                                "agent_eval.agent.release": "test-managed-judge-release",
                            "agent_eval.execution.origin": "sdk_task",
                        },
                    }
                ],
            },
        },
        headers=headers(api_settings),
    )
    assert ingested.status_code == 201

    dispatches: list[dict[str, str]] = []

    def capture_dispatch(*, redis_url: str, score_id: str) -> str:
        dispatches.append({"redis_url": redis_url, "score_id": score_id})
        return "managed-celery-task"

    monkeypatch.setattr(
        "agent_eval_api.evaluation.dispatch.dispatch_managed_judge",
        capture_dispatch,
    )
    finalized = client.post(
        f"/projects/project-1/experiments/{run_id}/finalize",
        headers=headers(api_settings),
    )
    assert finalized.status_code == 200
    assert finalized.json()["status"] == "running"
    score = session.scalar(select(ScoreRecord).where(ScoreRecord.run_id == run_id))
    assert score is not None
    assert score.status == "not_run"
    assert score.raw_result["state"] == "dispatched"
    assert dispatches == [{"redis_url": api_settings.redis_url, "score_id": score.id}]

    finalized_again = client.post(
        f"/projects/project-1/experiments/{run_id}/finalize",
        headers=headers(api_settings),
    )
    assert finalized_again.status_code == 200
    assert finalized_again.json()["status"] == "running"
    assert len(dispatches) == 1
    return client, worker_settings, session, run_id, score.id


def _provider_response() -> SimpleNamespace:
    return SimpleNamespace(
        id="deepseek-request-1",
        model="deepseek-chat",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps(
                        {"score": 0.9, "explanation": "The answer matches."}
                    )
                ),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=40,
            total_tokens=140,
            prompt_tokens_details={"cached_tokens": 20},
        ),
    )


def test_worker_decrypts_provider_and_replaces_pending_score_once(
    managed_client: tuple[TestClient, Settings, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject_external_judge(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("managed Provider Judge must not call an external evaluator")

    monkeypatch.setattr(
        "agent_eval_api.evaluation.judge.call_external_judge",
        reject_external_judge,
    )
    _, worker_settings, session, run_id, score_id = _prepare_pending_score(
        managed_client, monkeypatch
    )
    provider_client = ProviderClient(_provider_response())
    constructor: dict[str, object] = {}

    def fake_openai(**kwargs: object) -> ProviderClient:
        constructor.update(kwargs)
        return provider_client

    monkeypatch.setattr("agent_eval_api.evaluation.managed_provider.OpenAI", fake_openai)

    result = execute_managed_judge(
        session,
        worker_settings,
        score_id=score_id,
        owner_id="worker-1",
    )

    assert result == {"status": "passed", "score_id": score_id}
    assert constructor["api_key"] == TEST_PROVIDER_KEY
    assert constructor["base_url"] == "https://api.deepseek.com/v1"
    assert provider_client.closed is True
    session.expire_all()
    score = session.get(ScoreRecord, score_id)
    run = session.get(EvaluationRunRecord, run_id)
    assert score is not None
    assert run is not None
    assert score.status == "passed"
    assert score.value == 0.9
    assert score.passed is True
    assert score.provenance["source"] == "platform_provider"
    assert score.provenance["protocol"] == "openai_compatible_chat_completions"
    assert score.provenance["connection_id"] == "managed-worker-provider"
    assert run.configuration_snapshot["evaluators"][0].get(
        "evaluator_connection"
    ) is None
    assert run.status == "completed"
    assert session.query(ScoreRecord).filter(ScoreRecord.run_id == run_id).count() == 1
    evaluator_spans = session.scalars(
        select(TraceSpanRecord).where(
            TraceSpanRecord.trace_id == score.trace_id,
            TraceSpanRecord.kind == "evaluator",
        )
    ).all()
    assert len(evaluator_spans) == 1
    assert evaluator_spans[0].usage == {
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "total_tokens": 140,
        "prompt_tokens_details": {"cached_tokens": 20},
    }
    assert evaluator_spans[0].cost == {
        "amount": 0.00049,
        "currency": "USD",
        "calculation": "configured_token_rates",
        "input_tokens": 80,
        "cached_input_tokens": 20,
        "output_tokens": 40,
    }
    persisted = json.dumps(
        {
            "explanation": score.explanation,
            "raw_response": score.raw_response,
            "raw_result": score.raw_result,
        }
    )
    assert TEST_PROVIDER_KEY not in persisted

    duplicate = execute_managed_judge(
        session,
        worker_settings,
        score_id=score_id,
        owner_id="worker-duplicate",
    )
    assert duplicate == {"status": "ignored", "score_id": score_id}
    assert len(provider_client.completions.requests) == 1
    assert session.query(ScoreRecord).filter(ScoreRecord.run_id == run_id).count() == 1


def test_retry_exhaustion_persists_error_and_gate_cannot_pass(
    managed_client: tuple[TestClient, Settings, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, worker_settings, session, run_id, score_id = _prepare_pending_score(
        managed_client, monkeypatch
    )
    timeout = APITimeoutError(
        request=httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions")
    )
    monkeypatch.setattr(
        "agent_eval_api.evaluation.managed_provider.OpenAI",
        lambda **_kwargs: ProviderClient(timeout),
    )

    with pytest.raises(ManagedJudgeRetryableError):
        execute_managed_judge(
            session,
            worker_settings,
            score_id=score_id,
            owner_id="worker-retry",
        )

    session.expire_all()
    pending = session.get(ScoreRecord, score_id)
    assert pending is not None
    assert pending.status == "not_run"
    assert pending.raw_result["state"] == "queued"
    assert TEST_PROVIDER_KEY not in str(pending.raw_result)

    exhausted = fail_managed_judge(
        session,
        worker_settings,
        score_id=score_id,
        owner_id="worker-terminal",
        message="managed Judge provider remained unavailable after retries",
    )
    assert exhausted == {"status": "error", "score_id": score_id}
    session.expire_all()
    score = session.get(ScoreRecord, score_id)
    run = session.get(EvaluationRunRecord, run_id)
    assert score is not None
    assert run is not None
    assert score.status == "error"
    assert score.passed is None
    assert run.status == "partial"
    assert TEST_PROVIDER_KEY not in str(score.raw_result)

    gate = client.post(
        f"/projects/project-1/runs/{run_id}/regression-gate",
        json={
            "rules": [
                {"metric_name": "managed_answer_quality", "minimum": 0.8}
            ]
        },
        headers=headers(worker_settings),
    )
    assert gate.status_code == 200
    assert gate.json()["status"] == "indeterminate"
    assert gate.json()["rules"][0]["status"] == "indeterminate"
    assert gate.json()["rules"][0]["reason"] == (
        "evaluation run did not complete successfully"
    )
