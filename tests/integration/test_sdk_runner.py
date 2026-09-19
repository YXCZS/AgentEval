from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from agent_eval import Client, ExperimentRunner, TaskResult
from fastapi.testclient import TestClient
from opentelemetry import trace
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from tests.live import tool_agent as live_tool_agent_module
from tests.live.provider_preflight import LiveProviderConfig
from tests.live.tool_agent import SYSTEM_PROMPT, LiveToolAgent
from tests.live.tool_dataset import (
    EVALUATOR_DEFINITIONS,
    TOOL_CASES,
    ensure_tool_acceptance_resources,
)

from agent_eval_api import auth
from agent_eval_api.auth import get_db, issue_dev_session, issue_project_key
from agent_eval_api.db import (
    AggregateMetricRecord,
    Base,
    DatasetVersionRecord,
    EvaluatorVersionRecord,
    ExperimentItemAttemptRecord,
    ProjectRecord,
    ScoreRecord,
    TraceRecord,
)
from agent_eval_api.main import create_app
from agent_eval_api.settings import Settings


class TestClientTransport(httpx.BaseTransport):
    __test__ = False

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.client.request(
            request.method,
            str(request.url),
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )


@pytest.fixture
def sdk_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, Settings, Session, str]]:
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
    project = ProjectRecord(id="project-1", name="one")
    raw_key, key_record = issue_project_key(project.id, settings)
    session.add_all([project, key_record])
    session.commit()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[auth.get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, settings, session, raw_key
    session.close()


def test_sdk_runs_user_task_and_persists_attempt_result_and_otel_trace(
    sdk_stack: tuple[TestClient, Settings, Session, str],
) -> None:
    api, settings, session, raw_key = sdk_stack
    browser_headers = {
        "X-Workspace-Session": issue_dev_session("project-1", settings)
    }
    dataset_response = api.post(
        "/projects/project-1/datasets",
        json={
            "name": "Real callback cases",
            "cases": [
                {
                    "id": "order-42",
                    "input": {"order_id": "42"},
                    "expected_state": {"status": "cancelled"},
                }
            ],
        },
        headers=browser_headers,
    )
    evaluator_response = api.post(
        "/projects/project-1/evaluators",
        json={
            "name": "task_success",
            "version": "1",
            "evaluator_type": "deterministic",
            "requires": ["expected_state"],
            "supported_agent_types": ["tool"],
            "score_min": 0,
            "score_max": 1,
            "direction": "higher_is_better",
            "default_threshold": 1,
            "config": {"comparison": "subset"},
        },
        headers=browser_headers,
    )
    assert dataset_response.status_code == 201
    assert evaluator_response.status_code == 201

    with Client(
        base_url="http://testserver",
        project_id="project-1",
        api_key=raw_key,
        transport=TestClientTransport(api),
    ) as client:
        dataset = client.get_dataset(
            dataset_response.json()["id"],
            version_id=dataset_response.json()["current_version_id"],
        )
        release = client.register_release(
            label="real-callback",
            agent_type="tool",
            release_identity="git:test-real-callback",
            source_revision="test-real-callback",
        )

        def user_owned_task(case: object) -> TaskResult:
            assert not hasattr(client, "model_api_key")
            return TaskResult(
                output={"status": "cancelled", "source": "user-callback"},
                usage={"input_tokens": 7, "output_tokens": 3},
                metadata={"runtime": "integration-test-user-process"},
            )

        result = ExperimentRunner(client).run(
            dataset=dataset,
            task=user_owned_task,
            release=release,
            evaluator_version_ids=[evaluator_response.json()["id"]],
            name="SDK integration",
        )

    assert result.experiment.status == "completed"
    assert result.items[0].output["source"] == "user-callback"
    attempt = session.scalar(select(ExperimentItemAttemptRecord))
    assert attempt is not None
    assert attempt.trace_id is not None
    assert attempt.usage == {"input_tokens": 7, "output_tokens": 3}
    trace = session.get(TraceRecord, attempt.trace_id)
    assert trace is not None
    assert trace.source == "sdk"
    assert trace.run_id == result.experiment.id
    assert trace.case_id == "order-42"
    assert trace.spans[0].attributes["agent_eval.agent.release"] == (
        "git:test-real-callback"
    )
    score = session.scalar(select(ScoreRecord))
    aggregate = session.scalar(select(AggregateMetricRecord))
    assert score is not None
    assert score.status == "passed"
    assert score.passed is True
    assert score.trace_id == trace.id
    assert aggregate is not None
    assert aggregate.valid_count == 1
    assert aggregate.missing_count == 0
    assert aggregate.pass_rate == 1.0


def test_live_tool_resources_are_versioned_and_idempotent(
    sdk_stack: tuple[TestClient, Settings, Session, str],
) -> None:
    api, _, session, raw_key = sdk_stack
    with httpx.Client(
        base_url="http://testserver",
        headers={"X-Project-Key": raw_key},
        transport=TestClientTransport(api),
    ) as client:
        first = ensure_tool_acceptance_resources(client, "project-1")
        second = ensure_tool_acceptance_resources(client, "project-1")
        version_response = client.get(
            f"/projects/project-1/datasets/{first.dataset_id}/versions/"
            f"{first.dataset_version_id}"
        )

    assert first == second
    assert version_response.status_code == 200
    stored_cases = version_response.json()["cases"]
    assert [case["id"] for case in stored_cases] == sorted(case["id"] for case in TOOL_CASES)
    assert all(case["criteria"] for case in stored_cases)
    assert all(case["expected_tools"] for case in stored_cases)
    assert all(case["expected_state"] for case in stored_cases)
    assert len(session.scalars(select(DatasetVersionRecord)).all()) == 1
    assert len(session.scalars(select(EvaluatorVersionRecord)).all()) == len(
        EVALUATOR_DEFINITIONS
    )


def test_live_tool_agent_protocol_persists_provider_selected_trajectory(
    sdk_stack: tuple[TestClient, Settings, Session, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, settings, session, raw_key = sdk_stack
    browser_headers = {
        "X-Workspace-Session": issue_dev_session("project-1", settings)
    }
    dataset_response = api.post(
        "/projects/project-1/datasets",
        json={
            "name": "Live Tool Agent protocol",
            "cases": [
                {
                    "id": "cancel-shipped-order",
                    "input": {
                        "order_id": "ORDER-1002",
                        "request": "Can I cancel this order?",
                    },
                    "expected_tools": [
                        {
                            "name": "check_cancellation_eligibility",
                            "arguments": {"order_id": "ORDER-1002"},
                            "order": 0,
                        }
                    ],
                    "expected_state": {"status": "shipped", "eligible": False},
                }
            ],
        },
        headers=browser_headers,
    )
    evaluator_response = api.post(
        "/projects/project-1/evaluators",
        json={
            "name": "tool_selection",
            "version": "1",
            "evaluator_type": "deterministic",
            "requires": ["expected_tools", "tool_calls"],
            "supported_agent_types": ["tool"],
            "score_min": 0,
            "score_max": 1,
            "direction": "higher_is_better",
            "default_threshold": 1,
            "config": {"metric": "tool_correctness", "ordered": True},
        },
        headers=browser_headers,
    )
    assert dataset_response.status_code == 201
    assert evaluator_response.status_code == 201

    def completion(
        request_id: str, content: str | None, *, with_tool: bool = False
    ) -> object:
        calls = []
        finish_reason = "stop"
        if with_tool:
            calls = [
                SimpleNamespace(
                    id="call-from-provider",
                    type="function",
                    function=SimpleNamespace(
                        name="check_cancellation_eligibility",
                        arguments='{"order_id":"ORDER-1002"}',
                    ),
                )
            ]
            finish_reason = "tool_calls"
        return SimpleNamespace(
            id=request_id,
            model="test-provider-model-release",
            usage=SimpleNamespace(
                prompt_tokens=11,
                completion_tokens=5,
                total_tokens=16,
            ),
            choices=[
                SimpleNamespace(
                    finish_reason=finish_reason,
                    message=SimpleNamespace(content=content, tool_calls=calls),
                )
            ],
        )

    class ScriptedCompletions:
        def __init__(self) -> None:
            self.responses = [
                completion("chatcmpl-tool", None, with_tool=True),
                completion(
                    "chatcmpl-final",
                    "ORDER-1002 has shipped and is not cancellable.",
                ),
            ]
            self.requests: list[dict[str, Any]] = []

        def create(self, **kwargs: Any) -> object:
            self.requests.append(kwargs)
            return self.responses.pop(0)

    scripted = ScriptedCompletions()
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=scripted),
        close=lambda: None,
    )
    monkeypatch.setattr(live_tool_agent_module, "OpenAI", lambda **_: fake_client)
    agent = LiveToolAgent(
        LiveProviderConfig(
            base_url="https://provider.example.test/v1",
            api_key="test-only-secret",
            model="configured-model",
            timeout_seconds=5,
        )
    )

    with Client(
        base_url="http://testserver",
        project_id="project-1",
        api_key=raw_key,
        transport=TestClientTransport(api),
    ) as client:
        dataset = client.get_dataset(
            dataset_response.json()["id"],
            version_id=dataset_response.json()["current_version_id"],
        )
        release = client.register_release(
            label="live-tool-protocol",
            agent_type="tool",
            release_identity="prompt:test-live-tool-protocol",
        )
        result = ExperimentRunner(client).run(
            dataset=dataset,
            task=agent.run,
            release=release,
            evaluator_version_ids=[evaluator_response.json()["id"]],
            name="Live Tool Agent protocol persistence",
            evidence_policy="tool_trajectory_required",
        )

    attempt = session.scalar(
        select(ExperimentItemAttemptRecord).where(
            ExperimentItemAttemptRecord.experiment_id == result.experiment.id
        )
    )
    assert attempt is not None
    assert attempt.evidence_status == "complete"
    assert attempt.evidence_reasons == []
    assert attempt.runtime_metadata["execution_origin"] == "real_llm"
    assert attempt.usage == {
        "input_tokens": 22,
        "output_tokens": 10,
        "total_tokens": 32,
    }
    assert attempt.trace_id is not None
    stored_trace = session.get(TraceRecord, attempt.trace_id)
    assert stored_trace is not None
    spans_by_kind: dict[str, list[Any]] = {}
    for span in stored_trace.spans:
        spans_by_kind.setdefault(span.kind, []).append(span)
    assert len(spans_by_kind["agent"]) == 1
    assert len(spans_by_kind["llm"]) == 2
    assert len(spans_by_kind["tool"]) == 1
    assert len(spans_by_kind["tool_result"]) == 1
    assert {span.attributes["gen_ai.response.id"] for span in spans_by_kind["llm"]} == {
        "chatcmpl-tool",
        "chatcmpl-final",
    }
    tool_span = spans_by_kind["tool"][0]
    tool_result_span = spans_by_kind["tool_result"][0]
    assert tool_span.name == "check_cancellation_eligibility"
    assert tool_span.input == {"order_id": "ORDER-1002"}
    assert tool_result_span.parent_span_id == tool_span.span_id
    assert tool_result_span.output["reason"] == "already_in_fulfillment"
    assert len(scripted.requests) == 2
    assert scripted.requests[1]["messages"][-1]["role"] == "tool"
    stored_score = session.scalar(
        select(ScoreRecord).where(ScoreRecord.run_id == result.experiment.id)
    )
    assert stored_score is not None
    assert stored_score.status == "passed"
    assert stored_score.value == 1


def test_sdk_scores_each_repetition_once_and_finalize_is_idempotent(
    sdk_stack: tuple[TestClient, Settings, Session, str],
) -> None:
    api, settings, session, raw_key = sdk_stack
    browser_headers = {
        "X-Workspace-Session": issue_dev_session("project-1", settings)
    }
    dataset_response = api.post(
        "/projects/project-1/datasets",
        json={
            "name": "Repeated real callback",
            "cases": [
                {
                    "id": "repeat-1",
                    "input": {"order_id": "42"},
                    "expected_state": {"status": "cancelled"},
                }
            ],
        },
        headers=browser_headers,
    )
    evaluator_response = api.post(
        "/projects/project-1/evaluators",
        json={
            "name": "task_success",
            "version": "1",
            "evaluator_type": "deterministic",
            "requires": ["expected_state"],
            "supported_agent_types": ["tool"],
            "score_min": 0,
            "score_max": 1,
            "direction": "higher_is_better",
            "default_threshold": 1,
            "config": {"comparison": "subset"},
        },
        headers=browser_headers,
    )
    assert dataset_response.status_code == 201
    assert evaluator_response.status_code == 201

    with Client(
        base_url="http://testserver",
        project_id="project-1",
        api_key=raw_key,
        transport=TestClientTransport(api),
    ) as client:
        dataset = client.get_dataset(
            dataset_response.json()["id"],
            version_id=dataset_response.json()["current_version_id"],
        )
        release = client.register_release(
            label="repeated-callback",
            agent_type="tool",
            release_identity="git:repeated-callback",
        )
        result = ExperimentRunner(client).run(
            dataset=dataset,
            task=lambda _case: {"status": "cancelled"},
            release=release,
            evaluator_version_ids=[evaluator_response.json()["id"]],
            name="Repeated SDK integration",
            repetitions=2,
        )
        repeated_finalize = client.finalize_experiment(result.experiment.id)

    assert repeated_finalize.status == "completed"
    attempts = session.scalars(
        select(ExperimentItemAttemptRecord).order_by(
            ExperimentItemAttemptRecord.repetition
        )
    ).all()
    scores = session.scalars(select(ScoreRecord).order_by(ScoreRecord.repetition)).all()
    aggregate = session.scalar(select(AggregateMetricRecord))
    assert [item.repetition for item in attempts] == [1, 2]
    assert [score.repetition for score in scores] == [1, 2]
    assert [score.attempt for score in scores] == [1, 1]
    assert [score.experiment_item_id for score in scores] == [
        attempts[0].id,
        attempts[1].id,
    ]
    assert all(score.status == "passed" for score in scores)
    assert aggregate is not None
    assert aggregate.valid_count == 2
    assert aggregate.missing_count == 0
    assert aggregate.pass_rate == 1.0


def test_missing_trace_evidence_propagates_to_comparison_attribution_and_gate(
    sdk_stack: tuple[TestClient, Settings, Session, str],
) -> None:
    api, settings, session, raw_key = sdk_stack
    browser_headers = {
        "X-Workspace-Session": issue_dev_session("project-1", settings)
    }
    dataset_response = api.post(
        "/projects/project-1/datasets",
        json={
            "name": "Evidence propagation",
            "cases": [
                {
                    "id": "evidence-1",
                    "input": {"order_id": "42"},
                    "expected_state": {"status": "cancelled"},
                }
            ],
        },
        headers=browser_headers,
    )
    evaluator_response = api.post(
        "/projects/project-1/evaluators",
        json={
            "name": "task_success",
            "version": "1",
            "evaluator_type": "deterministic",
            "requires": ["expected_state"],
            "supported_agent_types": ["tool"],
            "score_min": 0,
            "score_max": 1,
            "direction": "higher_is_better",
            "default_threshold": 1,
            "config": {"comparison": "subset"},
        },
        headers=browser_headers,
    )
    evaluator_id = evaluator_response.json()["id"]

    with Client(
        base_url="http://testserver",
        project_id="project-1",
        api_key=raw_key,
        transport=TestClientTransport(api),
    ) as client:
        dataset = client.get_dataset(
            dataset_response.json()["id"],
            version_id=dataset_response.json()["current_version_id"],
        )
        baseline_release = client.register_release(
            label="baseline",
            agent_type="tool",
            release_identity="git:evidence-baseline",
        )
        baseline = ExperimentRunner(client).run(
            dataset=dataset,
            task=lambda _case: {"status": "cancelled"},
            release=baseline_release,
            evaluator_version_ids=[evaluator_id],
            name="Evidence baseline",
        ).experiment

        candidate_release = client.register_release(
            label="candidate",
            agent_type="tool",
            release_identity="git:evidence-candidate",
        )
        candidate = client.create_experiment(
            name="Evidence candidate",
            dataset_version_id=dataset.version.id,
            release_id=candidate_release.id,
            evaluator_version_ids=[evaluator_id],
            evidence_policy="trace_required",
            baseline_experiment_id=baseline.id,
            execution_options={
                "concurrency": 1,
                "repetitions": 1,
                "timeout_seconds": 30,
                "max_retries": 0,
                "retry_backoff_seconds": 0,
            },
        )
        item = client.start_item(
            candidate.id,
            case_id=dataset.version.cases[0].id,
            repetition=1,
            attempt=1,
            external_run_id="candidate-without-trace",
        )
        client.complete_item(
            candidate.id,
            item.id,
            output={"status": "cancelled", "source": "real-callback-without-telemetry"},
        )
        candidate = client.finalize_experiment(candidate.id)

    assert candidate.status == "partial"
    candidate_attempt = session.scalar(
        select(ExperimentItemAttemptRecord).where(
            ExperimentItemAttemptRecord.experiment_id == candidate.id
        )
    )
    candidate_score = session.scalar(
        select(ScoreRecord).where(ScoreRecord.run_id == candidate.id)
    )
    assert candidate_attempt is not None
    assert candidate_attempt.evidence_status == "incomplete"
    assert candidate_attempt.output["source"] == "real-callback-without-telemetry"
    assert candidate_score is not None
    assert candidate_score.status == "missing"
    assert candidate_score.passed is None

    comparison = api.post(
        "/projects/project-1/comparisons",
        json={"run_ids": [baseline.id, candidate.id]},
        headers=browser_headers,
    )
    assert comparison.status_code == 200
    comparison_body = comparison.json()
    candidate_point = comparison_body["metric_comparisons"][0]["points"][1]
    assert candidate_point["valid_count"] == 0
    assert candidate_point["missing_count"] == 1
    assert comparison_body["new_failures"][0]["first_error"]["category"] == (
        "indeterminate"
    )

    gate = api.post(
        f"/projects/project-1/runs/{candidate.id}/regression-gate",
        json={"rules": [{"metric_name": "task_success", "minimum": 1}]},
        headers=browser_headers,
    )
    assert gate.status_code == 200
    assert gate.json()["status"] == "indeterminate"
    assert gate.json()["rules"][0]["status"] == "indeterminate"


def test_sdk_baseline_candidate_comparison_attribution_and_gate_use_persisted_evidence(
    sdk_stack: tuple[TestClient, Settings, Session, str],
) -> None:
    api, settings, session, raw_key = sdk_stack
    browser_headers = {
        "X-Workspace-Session": issue_dev_session("project-1", settings)
    }
    dataset_response = api.post(
        "/projects/project-1/datasets",
        json={
            "name": "Tool regression",
            "cases": [
                {
                    "id": "tool-case-1",
                    "input": {"order_id": "42"},
                    "expected_tools": [
                        {"name": "cancel_order", "arguments": {"order_id": "42"}}
                    ],
                    "expected_state": {"status": "cancelled"},
                }
            ],
        },
        headers=browser_headers,
    )
    evaluator_response = api.post(
        "/projects/project-1/evaluators",
        json={
            "name": "tool_selection",
            "version": "1",
            "evaluator_type": "deterministic",
            "requires": ["expected_tools", "tool_calls"],
            "supported_agent_types": ["tool"],
            "score_min": 0,
            "score_max": 1,
            "direction": "higher_is_better",
            "default_threshold": 1,
            "config": {"metric": "tool_correctness"},
        },
        headers=browser_headers,
    )
    assert dataset_response.status_code == 201
    assert evaluator_response.status_code == 201
    evaluator_id = evaluator_response.json()["id"]
    user_tracer = trace.get_tracer("sdk-regression-agent")

    def task_for(tool_name: str) -> Callable[[object], TaskResult]:
        def task(case: object) -> TaskResult:
            with user_tracer.start_as_current_span("chat.completions") as llm_span:
                llm_span.set_attribute("openinference.span.kind", "LLM")
                llm_span.set_attribute("gen_ai.request.model", "real-model-id")
                llm_span.set_attribute("agent_eval.usage.present", True)
            with user_tracer.start_as_current_span(tool_name) as tool_span:
                tool_span.set_attribute("openinference.span.kind", "TOOL")
                tool_span.set_attribute("tool.name", tool_name)
                tool_span.set_attribute(
                    "agent_eval.input", json.dumps({"order_id": "42"})
                )
                with user_tracer.start_as_current_span(f"{tool_name}.result") as result_span:
                    result_span.set_attribute("openinference.span.kind", "TOOL_RESULT")
                    result_span.set_attribute("tool.name", tool_name)
                    result_span.set_attribute(
                        "agent_eval.output", json.dumps({"status": "cancelled"})
                    )
            return TaskResult(
                output={"status": "cancelled"},
                usage={"input_tokens": 5, "output_tokens": 2},
            )

        return task

    with Client(
        base_url="http://testserver",
        project_id="project-1",
        api_key=raw_key,
        transport=TestClientTransport(api),
    ) as client:
        dataset = client.get_dataset(
            dataset_response.json()["id"],
            version_id=dataset_response.json()["current_version_id"],
        )
        baseline_release = client.register_release(
            label="baseline",
            agent_type="tool",
            release_identity="git:tool-baseline",
        )
        baseline = ExperimentRunner(client).run(
            dataset=dataset,
            task=task_for("cancel_order"),
            release=baseline_release,
            evaluator_version_ids=[evaluator_id],
            name="Tool baseline",
            evidence_policy="tool_trajectory_required",
        ).experiment
        candidate_release = client.register_release(
            label="candidate",
            agent_type="tool",
            release_identity="git:tool-candidate",
        )
        candidate = ExperimentRunner(client).run(
            dataset=dataset,
            task=task_for("lookup_order"),
            release=candidate_release,
            evaluator_version_ids=[evaluator_id],
            name="Tool candidate",
            evidence_policy="tool_trajectory_required",
            baseline_experiment_id=baseline.id,
        ).experiment

    stored_items = session.scalars(
        select(ExperimentItemAttemptRecord).where(
            ExperimentItemAttemptRecord.experiment_id.in_([baseline.id, candidate.id])
        )
    ).all()
    stored_scores = session.scalars(
        select(ScoreRecord).where(ScoreRecord.run_id.in_([baseline.id, candidate.id]))
    ).all()
    assert [(item.evidence_status, item.evidence_reasons) for item in stored_items] == [
        ("complete", []),
        ("complete", []),
    ]
    score_by_run = {score.run_id: score for score in stored_scores}
    assert (score_by_run[baseline.id].status, score_by_run[baseline.id].value) == (
        "passed",
        1,
    )
    assert (score_by_run[candidate.id].status, score_by_run[candidate.id].value) == (
        "failed",
        0,
    )

    comparison = api.post(
        "/projects/project-1/comparisons",
        json={"run_ids": [baseline.id, candidate.id]},
        headers=browser_headers,
    )
    assert comparison.status_code == 200
    body = comparison.json()
    metric = body["metric_comparisons"][0]
    assert metric["comparable"] is True
    assert metric["points"][0]["average"] == 1
    assert metric["points"][1]["average"] == 0
    assert metric["points"][1]["delta_average"] == -1
    assert body["new_failures"][0]["first_error"]["category"] == "tool_selection"
    first_error = body["new_failures"][0]["first_error"]
    assert first_error["baseline_span_id"]
    assert first_error["candidate_span_id"]
    assert first_error["evidence"][0]["field"] in {"kind", "tool_name"}

    gate = api.post(
        f"/projects/project-1/runs/{candidate.id}/regression-gate",
        json={
            "rules": [
                {
                    "metric_name": "tool_selection",
                    "minimum": 1,
                    "require_all_passed": True,
                }
            ]
        },
        headers=browser_headers,
    )
    assert gate.status_code == 200
    assert gate.json()["status"] == "failed"
    assert gate.json()["rules"][0]["failed_case_ids"] == ["tool-case-1"]


def test_live_tool_baseline_candidate_full_platform_workflow(
    sdk_stack: tuple[TestClient, Settings, Session, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, _, session, raw_key = sdk_stack
    transport = TestClientTransport(api)
    provider = LiveProviderConfig(
        base_url="https://provider.example.test/v1",
        api_key="test-only-secret",
        model="configured-model",
        timeout_seconds=5,
    )

    # Build a data-driven router from the real TOOL_CASES so the scripted LLM
    # always selects the tool each case expects, independent of dataset size.
    expected_tool_by_case: dict[tuple[str, str], str] = {
        (str(case["input"]["order_id"]), str(case["input"]["request"]).strip()): str(
            case["expected_tools"][0]["name"]
        )
        for case in TOOL_CASES
    }

    class ScriptedCompletions:
        def __init__(self) -> None:
            self.request_count = 0

        def create(self, **kwargs: Any) -> object:
            self.request_count += 1
            messages = kwargs["messages"]
            user_content = str(messages[1]["content"])
            request_id = f"chatcmpl-scripted-{self.request_count}"
            usage = SimpleNamespace(
                prompt_tokens=12,
                completion_tokens=4,
                total_tokens=16,
            )
            if messages[-1]["role"] == "tool":
                return SimpleNamespace(
                    id=request_id,
                    model="provider-model-release",
                    usage=usage,
                    choices=[
                        SimpleNamespace(
                            finish_reason="stop",
                            message=SimpleNamespace(
                                content=f"Grounded answer for {user_content.splitlines()[0]}",
                                tool_calls=[],
                            ),
                        )
                    ],
                )

            order_id = re.search(r"ORDER-\d{4}", user_content).group(0)
            request_text = user_content.split("\n", 1)[1].split(":", 1)[1].strip()
            tool_name = expected_tool_by_case[(order_id, request_text)]
            call = SimpleNamespace(
                id=f"call-scripted-{self.request_count}",
                type="function",
                function=SimpleNamespace(
                    name=tool_name,
                    arguments=json.dumps({"order_id": order_id}),
                ),
            )
            return SimpleNamespace(
                id=request_id,
                model="provider-model-release",
                usage=usage,
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        message=SimpleNamespace(content=None, tool_calls=[call]),
                    )
                ],
            )

    scripted = ScriptedCompletions()
    monkeypatch.setattr(
        live_tool_agent_module,
        "OpenAI",
        lambda **_: SimpleNamespace(
            chat=SimpleNamespace(completions=scripted),
            close=lambda: None,
        ),
    )

    with httpx.Client(
        base_url="http://testserver",
        headers={"X-Project-Key": raw_key},
        transport=transport,
    ) as platform_http:
        resources = ensure_tool_acceptance_resources(platform_http, "project-1")

    with Client(
        base_url="http://testserver",
        project_id="project-1",
        api_key=raw_key,
        transport=transport,
    ) as client:
        dataset = client.get_dataset(
            resources.dataset_id,
            version_id=resources.dataset_version_id,
        )
        baseline_release = client.register_release(
            label="full-tool-baseline",
            agent_type="tool",
            release_identity="test:full-tool-baseline",
        )
        baseline = ExperimentRunner(client).run(
            dataset=dataset,
            task=LiveToolAgent(provider, system_prompt=SYSTEM_PROMPT).run,
            release=baseline_release,
            evaluator_version_ids=list(resources.evaluator_version_ids),
            name="Full Tool baseline",
            evidence_policy="tool_trajectory_required",
        )
        candidate_release = client.register_release(
            label="full-tool-candidate",
            agent_type="tool",
            release_identity="test:full-tool-candidate",
        )
        candidate = ExperimentRunner(client).run(
            dataset=dataset,
            task=LiveToolAgent(
                provider,
                system_prompt=(
                    SYSTEM_PROMPT
                    + "\nChoose the most specific read-only tool and ground the answer."
                ),
            ).run,
            release=candidate_release,
            evaluator_version_ids=list(resources.evaluator_version_ids),
            name="Full Tool candidate",
            evidence_policy="tool_trajectory_required",
            baseline_experiment_id=baseline.experiment.id,
        )

    run_ids = [baseline.experiment.id, candidate.experiment.id]
    attempts = session.scalars(
        select(ExperimentItemAttemptRecord).where(
            ExperimentItemAttemptRecord.experiment_id.in_(run_ids)
        )
    ).all()
    scores = session.scalars(
        select(ScoreRecord).where(ScoreRecord.run_id.in_(run_ids))
    ).all()
    traces = session.scalars(
        select(TraceRecord).where(TraceRecord.run_id.in_(run_ids))
    ).all()

    assert baseline.experiment.dataset_version_id == resources.dataset_version_id
    assert candidate.experiment.dataset_version_id == resources.dataset_version_id
    assert candidate.experiment.baseline_run_id == baseline.experiment.id
    case_count = len(TOOL_CASES)
    assert len(attempts) == case_count * 2
    assert all(
        attempt.status == "completed" and attempt.evidence_status == "complete"
        for attempt in attempts
    )
    assert len(scores) == case_count * 3 * 2
    failed_scores = [
        {
            "run_id": score.run_id,
            "case_id": score.case_id,
            "metric": score.metric_name,
            "status": score.status,
            "value": score.value,
            "explanation": score.explanation,
            "evidence": score.evidence,
        }
        for score in scores
        if score.status != "passed" or score.value != 1
    ]
    assert failed_scores == []
    assert len(traces) == case_count * 2
    assert scripted.request_count == case_count * 4

    comparison = api.post(
        "/projects/project-1/comparisons",
        json={"run_ids": run_ids},
        headers={"X-Project-Key": raw_key},
    )
    assert comparison.status_code == 200
    comparison_body = comparison.json()
    assert comparison_body["baseline_run_id"] == baseline.experiment.id
    assert comparison_body["new_failures"] == []
    assert comparison_body["missing_evidence"] == []
    assert len(comparison_body["metric_comparisons"]) == 3
    assert all(
        metric["points"][1]["delta_average"] == 0
        for metric in comparison_body["metric_comparisons"]
    )

    gate = api.post(
        f"/projects/project-1/runs/{candidate.experiment.id}/regression-gate",
        json={
            "rules": [
                {
                    "metric_name": definition["name"],
                    "minimum": 1,
                    "require_all_passed": True,
                }
                for definition in EVALUATOR_DEFINITIONS
            ]
        },
        headers={"X-Project-Key": raw_key},
    )
    assert gate.status_code == 200
    assert gate.json()["status"] == "passed"
