from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from agent_eval_api import auth
from agent_eval_api.auth import get_db, issue_dev_session
from agent_eval_api.contracts import ScoreSource, ScoreStatus
from agent_eval_api.db import (
    AgentRecord,
    AgentVersionRecord,
    Base,
    CaseExecutionRecord,
    DatasetCaseRecord,
    DatasetRecord,
    DatasetVersionRecord,
    EvaluationRunRecord,
    ProjectRecord,
    ScoreRecord,
    TraceRecord,
)
from agent_eval_api.main import create_app
from agent_eval_api.settings import Settings


@pytest.fixture
def score_client(
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


def create_trace(client: TestClient, settings: Settings, trace_id: str = "online-trace") -> None:
    response = client.post(
        "/projects/project-1/traces",
        json={
            "trace_id": trace_id,
            "status": "completed",
            "source": "agent-sdk",
            "spans": [
                {
                    "span_id": "root",
                    "trace_id": trace_id,
                    "kind": "agent",
                    "name": "support-agent",
                    "status": "completed",
                    "started_at": "2026-01-01T00:00:00Z",
                    "ended_at": "2026-01-01T00:00:01Z",
                },
                {
                    "span_id": "tool-1",
                    "trace_id": trace_id,
                    "parent_span_id": "root",
                    "kind": "tool",
                    "name": "search_orders",
                    "status": "completed",
                    "started_at": "2026-01-01T00:00:00.100Z",
                    "ended_at": "2026-01-01T00:00:00.500Z",
                    "output": {"api_key": "secret-value", "order": "42"},
                },
            ],
        },
        headers=headers(settings),
    )
    assert response.status_code == 201


def create_evaluator(
    client: TestClient,
    settings: Settings,
    project_id: str = "project-1",
    evaluator_type: str = "deterministic",
) -> str:
    response = client.post(
        f"/projects/{project_id}/evaluators",
        json={
            "name": "tool_quality",
            "version": "1.0.0",
            "evaluator_type": evaluator_type,
            "supported_agent_types": ["tool"],
            "score_min": 0,
            "score_max": 1,
            "direction": "higher_is_better",
            "default_threshold": 0.8,
        },
        headers=headers(settings, project_id),
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_online_score_links_trace_and_span_and_is_returned_in_trace_detail(
    score_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = score_client
    create_trace(client, settings)
    evaluator_id = create_evaluator(client, settings)

    response = client.post(
        "/projects/project-1/traces/online-trace/scores",
        json={
            "evaluator_version_id": evaluator_id,
            "source": "deterministic",
            "span_id": "tool-1",
            "status": "passed",
            "value": 1,
            "passed": True,
            "explanation": "Tool selected correctly",
            "evidence": [{"authorization": "Bearer hidden-value", "span_id": "tool-1"}],
        },
        headers=headers(settings),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["trace_id"] == "online-trace"
    assert body["span_id"] == "tool-1"
    assert body["source"] == "deterministic"
    assert body["evidence"] == [
        {"authorization": {"__agent_eval_redacted": True}, "span_id": "tool-1"}
    ]

    detail = client.get(
        "/projects/project-1/traces/online-trace", headers=headers(settings)
    )
    assert detail.status_code == 200
    assert detail.json()["scores"][0]["id"] == body["id"]
    assert detail.json()["scores"][0]["span_id"] == "tool-1"
    stored = session.scalar(select(ScoreRecord).where(ScoreRecord.id == body["id"]))
    assert stored is not None
    assert stored.trace_id != "online-trace"
    assert stored.source == ScoreSource.DETERMINISTIC.value


def test_online_judge_score_preserves_provenance_and_raw_response(
    score_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = score_client
    create_trace(client, settings, "judge-trace")
    evaluator_id = create_evaluator(client, settings)
    response = client.post(
        "/projects/project-1/traces/judge-trace/scores",
        json={
            "evaluator_version_id": evaluator_id,
            "source": "llm_judge",
            "status": "passed",
            "value": 0.9,
            "passed": True,
            "provenance": {
                "evaluator_version": "quality@1.0.0",
                "model": "judge-model",
                "model_release": "release-1",
                "rubric_version": "rubric-1",
                "prompt_template_version": "template-1",
            },
            "raw_response": {"score": 0.9, "decision": "pass"},
        },
        headers=headers(settings),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["provenance"]["model_release"] == "release-1"
    assert body["raw_response"] == {"score": 0.9, "decision": "pass"}
    stored = session.scalar(select(ScoreRecord).where(ScoreRecord.id == body["id"]))
    assert stored is not None
    assert stored.judge_model == "judge-model"
    assert stored.provenance["prompt_template_version"] == "template-1"
    assert stored.raw_response == {"score": 0.9, "decision": "pass"}


def test_online_score_rejects_cross_project_evaluator_and_unknown_span(
    score_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = score_client
    create_trace(client, settings)
    evaluator_id = create_evaluator(client, settings, "project-2")

    cross_project = client.post(
        "/projects/project-1/traces/online-trace/scores",
        json={
            "evaluator_version_id": evaluator_id,
            "source": "deterministic",
            "status": "missing",
        },
        headers=headers(settings),
    )
    assert cross_project.status_code == 404

    evaluator_id = create_evaluator(client, settings)
    unknown_span = client.post(
        "/projects/project-1/traces/online-trace/scores",
        json={
            "evaluator_version_id": evaluator_id,
            "source": "deterministic",
            "span_id": "missing-span",
            "status": "missing",
        },
        headers=headers(settings),
    )
    assert unknown_span.status_code == 404


def test_human_annotation_creates_separate_score_from_completed_automated_score(
    score_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = score_client
    create_trace(client, settings, "review-trace")
    evaluator_id = create_evaluator(client, settings, evaluator_type="human")
    trace = session.scalar(select(TraceRecord).where(TraceRecord.trace_id == "review-trace"))
    assert trace is not None
    run = EvaluationRunRecord(
        id="run-review",
        project_id="project-1",
        agent_version_id="agent-version",
        dataset_version_id="dataset-version",
        status="completed",
        configuration_snapshot={"evaluators": [{"id": evaluator_id}]},
    )
    # The queue API only needs an existing run/case pair. Use direct records so
    # this test isolates score provenance rather than the experiment workflow.
    agent = AgentRecord(id="agent", project_id="project-1", name="agent", agent_type="tool")
    agent_version = AgentVersionRecord(
        id="agent-version",
        project_id="project-1",
        agent=agent,
        version=1,
        label="v1",
        agent_type="tool",
        release_identity="git-sha-v1",
        endpoint_config={"url": "https://agent.example.test"},
    )
    dataset = DatasetRecord(id="dataset", project_id="project-1", name="dataset")
    dataset_version = DatasetVersionRecord(
        id="dataset-version", dataset=dataset, version=1, metadata_json={}
    )
    case = DatasetCaseRecord(
        id="case-row", dataset_version=dataset_version, case_key="case-review", input_json="hello"
    )
    session.add_all([agent, agent_version, dataset, dataset_version, case, run])
    session.flush()
    session.add(
        CaseExecutionRecord(
            id="execution-review",
            run_id=run.id,
            case_id=case.id,
            status="completed",
            trace_id=trace.id,
            output="agent output",
        )
    )
    session.add(
        ScoreRecord(
            id="auto-score",
            run_id=run.id,
            case_id=case.case_key,
            evaluator_version_id=evaluator_id,
            trace_id=trace.id,
            metric_name="tool_quality",
            source=ScoreSource.DETERMINISTIC.value,
            status=ScoreStatus.FAILED.value,
            value=0,
            passed=False,
            direction="higher_is_better",
        )
    )
    session.commit()

    queue = client.post(
        "/projects/project-1/annotation-queues",
        json={"name": "review", "evaluator_version_id": evaluator_id},
        headers=headers(settings),
    )
    assert queue.status_code == 201
    item = client.post(
        f"/projects/project-1/annotation-queues/{queue.json()['id']}/items",
        json={"run_id": run.id, "case_id": case.case_key},
        headers=headers(settings),
    )
    assert item.status_code == 201
    score = client.put(
        f"/projects/project-1/annotation-queues/{queue.json()['id']}/items/{item.json()['id']}/score",
        json={"value": 1, "passed": True, "label": "reviewed"},
        headers=headers(settings),
    )
    assert score.status_code == 200
    assert score.json()["source"] == ScoreSource.HUMAN.value
    rows = session.scalars(
        select(ScoreRecord).where(
            ScoreRecord.run_id == run.id,
            ScoreRecord.case_id == case.case_key,
            ScoreRecord.evaluator_version_id == evaluator_id,
        )
    ).all()
    assert {(row.source, row.passed) for row in rows} == {
        (ScoreSource.DETERMINISTIC.value, False),
        (ScoreSource.HUMAN.value, True),
    }
