import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from agent_eval_api import auth
from agent_eval_api.auth import get_db, issue_dev_session, issue_project_key
from agent_eval_api.db import Base, ProjectRecord, TraceRecord, TraceSpanRecord
from agent_eval_api.main import create_app
from agent_eval_api.settings import Settings

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture
def trace_client() -> Iterator[tuple[TestClient, Settings, Session]]:
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


def trace_payload() -> dict[str, object]:
    started_at = datetime.now(UTC)
    return {
        "trace_id": "trace-1",
        "case_id": "order-42",
        "status": "completed",
        "source": "http-agent",
        "extensions": {"vendor.trace_id": "external-42"},
        "spans": [
            {
                "span_id": "tool-result",
                "trace_id": "trace-1",
                "parent_span_id": "tool-call",
                "kind": "tool_result",
                "name": "search_order result",
                "status": "completed",
                "started_at": (started_at + timedelta(seconds=2)).isoformat(),
                "output": {"status": "shipped"},
                "usage": {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11},
                "cost": {"total": 0.0004, "currency": "USD"},
            },
            {
                "span_id": "agent",
                "trace_id": "trace-1",
                "kind": "agent",
                "name": "order-agent",
                "status": "completed",
                "started_at": started_at.isoformat(),
            },
            {
                "span_id": "tool-call",
                "trace_id": "trace-1",
                "parent_span_id": "agent",
                "kind": "tool",
                "name": "search_order",
                "status": "completed",
                "started_at": (started_at + timedelta(seconds=1)).isoformat(),
                "input": {"order_id": "42"},
                "attributes": {"tool.name": "search_order"},
            },
        ],
    }


def test_canonical_trace_persists_hierarchy_and_is_project_scoped(
    trace_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = trace_client
    created = client.post(
        "/projects/project-1/traces", json=trace_payload(), headers=headers(settings)
    )

    assert created.status_code == 201
    assert [span["span_id"] for span in created.json()["spans"]] == [
        "agent",
        "tool-call",
        "tool-result",
    ]
    assert created.json()["spans"][2]["parent_span_id"] == "tool-call"

    detail = client.get("/projects/project-1/traces/trace-1", headers=headers(settings))
    assert detail.status_code == 200
    assert detail.json()["extensions"]["vendor.trace_id"] == "external-42"
    result_span = detail.json()["spans"][2]
    assert result_span["usage"] == {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11}
    assert result_span["cost"] == {"total": 0.0004, "currency": "USD"}

    forbidden = client.get("/projects/project-2/traces/trace-1", headers=headers(settings))
    assert forbidden.status_code == 401


def test_trace_ingestion_requires_project_authentication(
    trace_client: tuple[TestClient, Settings, Session],
) -> None:
    client, _, _ = trace_client

    response = client.post("/projects/project-1/traces", json=trace_payload())

    assert response.status_code == 401


def test_external_trace_ingestion_normalizes_openinference_fields(
    trace_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = trace_client
    response = client.post(
        "/projects/project-1/traces/ingest",
        json={
            "source": "openinference",
            "payload": {
                "trace_id": "external-trace-1",
                "spans": [
                    {
                        "span_id": "span-1",
                        "name": "chat.completions",
                        "startTimeUnixNano": "1720000000000000000",
                        "attributes": {
                            "openinference.span.kind": "LLM",
                            "input.value": '{"question":"Where is order 42?"}',
                            "gen_ai.request.model": "gpt-test",
                        },
                    }
                ],
            },
        },
        headers=headers(settings),
    )

    assert response.status_code == 201
    span = response.json()["spans"][0]
    assert span["kind"] == "llm"
    assert span["input"] == {"question": "Where is order 42?"}
    assert span["attributes"]["gen_ai.request.model"] == "gpt-test"


def test_otlp_http_ingestion_normalizes_resource_scope_and_span_fields(
    trace_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = trace_client
    payload = json.loads((FIXTURES / "otlp_http_trace.json").read_text())

    response = client.post(
        "/projects/project-1/traces/otlp", json=payload, headers=headers(settings)
    )

    assert response.status_code == 201
    trace = response.json()
    assert trace["trace_id"] == "otlp-trace-1"
    assert trace["source"] == "otlp"
    spans = {span["span_id"]: span for span in trace["spans"]}
    assert spans["agent-span"]["attributes"]["service.name"] == "order-agent"
    assert spans["tool-span"]["parent_span_id"] == "agent-span"
    assert spans["tool-span"]["kind"] == "tool"
    assert spans["tool-span"]["input"] == {"order_id": "42"}
    assert spans["tool-result-span"]["output"] == {"status": "shipped"}
    assert spans["llm-span"]["usage"] == {
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
    }
    assert spans["llm-span"]["extensions"]["otlp.scope"]["scope"]["vendor.scope"] == "kept"


def test_otlp_http_ingestion_requires_auth_and_rejects_malformed_envelopes(
    trace_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = trace_client

    unauthenticated = client.post(
        "/projects/project-1/traces/otlp", json={"resourceSpans": []}
    )
    assert unauthenticated.status_code == 401

    malformed = client.post(
        "/projects/project-1/traces/otlp",
        json={"resourceSpans": [{"scopeSpans": {}}]},
        headers=headers(settings),
    )
    assert malformed.status_code == 422
    assert "scopeSpans must be an array" in malformed.json()["detail"]


def test_trace_credentials_are_redacted_and_large_fields_are_referenced(
    trace_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = trace_client
    settings.trace_max_field_bytes = 96
    payload = trace_payload()
    span = payload["spans"][0]
    assert isinstance(span, dict)
    span["input"] = {"api_key": "secret-key", "request": "Where is order 42?"}
    span["output"] = "x" * 100
    span["error"] = {"message": "Bearer very-secret-token"}
    span["attributes"] = {"authorization": "Bearer another-secret-token"}
    span["extensions"] = {"credentials": {"password": "not-for-storage"}}

    response = client.post(
        "/projects/project-1/traces", json=payload, headers=headers(settings)
    )

    assert response.status_code == 201
    response_text = response.text
    assert "secret-key" not in response_text
    assert "very-secret-token" not in response_text
    assert "another-secret-token" not in response_text
    persisted = response.json()["spans"][2]
    assert persisted["input"]["api_key"] == {"__agent_eval_redacted": True}
    assert persisted["output"]["__agent_eval_content_ref"]["kind"] == "truncated"
    assert persisted["error"]["message"] == {"__agent_eval_redacted": True}
    assert persisted["attributes"]["authorization"] == {"__agent_eval_redacted": True}
    assert persisted["extensions"]["credentials"] == {"__agent_eval_redacted": True}
    assert response.json()["extensions"]["agent_eval.privacy"] == {
        "redacted_fields": 4,
        "truncated_fields": 1,
    }
    stored = session.scalar(
        select(TraceRecord).where(
            TraceRecord.project_id == "project-1", TraceRecord.trace_id == "trace-1"
        )
    )
    assert stored is not None
    stored_text = json.dumps(
        {
            "extensions": stored.extensions,
            "spans": [
                {
                    "input": span.input,
                    "output": span.output,
                    "error": span.error,
                    "attributes": span.attributes,
                    "extensions": span.extensions,
                }
                for span in session.query(TraceSpanRecord).filter_by(trace_id="trace-1")
            ],
        }
    )
    assert "secret-key" not in stored_text
    assert "very-secret-token" not in stored_text


def test_historical_trace_is_redacted_before_detail_and_dataset_access(
    trace_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = trace_client
    started_at = datetime.now(UTC)
    historical = TraceRecord(
        id="historical-trace",
        project_id="project-1",
        status="completed",
        source="legacy",
        extensions={"legacy": "safe"},
    )
    historical.spans = [
        TraceSpanRecord(
            trace_id="historical-trace",
            span_id="agent",
            kind="agent",
            name="legacy-agent",
            status="completed",
            started_at=started_at,
            input={"authorization": "Bearer historical-secret"},
            output={"answer": "safe"},
        )
    ]
    session.add(historical)
    session.commit()

    response = client.get(
        "/projects/project-1/traces/historical-trace", headers=headers(settings)
    )

    assert response.status_code == 200
    assert "historical-secret" not in response.text
    assert response.json()["spans"][0]["input"]["authorization"] == {
        "__agent_eval_redacted": True
    }


def test_trace_list_filters_and_timeline_are_project_scoped(
    trace_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = trace_client
    first = client.post(
        "/projects/project-1/traces", json=trace_payload(), headers=headers(settings)
    )
    assert first.status_code == 201
    second_payload = trace_payload()
    second_payload["trace_id"] = "trace-2"
    second_payload["case_id"] = "order-43"
    second_payload["status"] = "failed"
    for span in second_payload["spans"]:
        assert isinstance(span, dict)
        span["trace_id"] = "trace-2"
        span["status"] = "failed"
    second = client.post(
        "/projects/project-1/traces", json=second_payload, headers=headers(settings)
    )
    assert second.status_code == 201

    filtered = client.get(
        "/projects/project-1/traces?case_id=order-43&status=failed",
        headers=headers(settings),
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    summary = filtered.json()["items"][0]
    assert summary["trace_id"] == "trace-2"
    assert summary["case_id"] == "order-43"
    assert summary["status"] == "failed"
    assert summary["span_count"] == 3
    assert summary["started_at"] is not None
    assert summary["ended_at"] is not None

    page = client.get(
        "/projects/project-1/traces?query=trace&limit=1&offset=1",
        headers=headers(settings),
    )
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert page.json()["offset"] == 1
    assert page.json()["limit"] == 1
    assert page.json()["next_offset"] is None
    assert len(page.json()["items"]) == 1

    timeline = client.get(
        "/projects/project-1/traces/trace-1/timeline", headers=headers(settings)
    )
    assert timeline.status_code == 200
    assert [span["span_id"] for span in timeline.json()["spans"]] == [
        "agent",
        "tool-call",
        "tool-result",
    ]
    assert timeline.json()["spans"][1]["depth"] == 1
    assert timeline.json()["spans"][2]["depth"] == 2

    forbidden = client.get(
        "/projects/project-2/traces?case_id=order-43", headers=headers(settings)
    )
    assert forbidden.status_code == 401


def test_trace_retry_is_idempotent_and_conflicting_content_is_rejected(
    trace_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = trace_client
    payload = trace_payload()

    first = client.post(
        "/projects/project-1/traces", json=payload, headers=headers(settings)
    )
    retry = client.post(
        "/projects/project-1/traces", json=payload, headers=headers(settings)
    )
    conflicting = json.loads(json.dumps(payload))
    conflicting["spans"][0]["output"] = {"status": "cancelled"}
    conflict = client.post(
        "/projects/project-1/traces", json=conflicting, headers=headers(settings)
    )

    assert first.status_code == 201
    assert retry.status_code == 201
    assert retry.json()["trace_id"] == first.json()["trace_id"]
    assert conflict.status_code == 409
    assert session.query(TraceRecord).count() == 1
    assert session.query(TraceSpanRecord).count() == 3


def test_trace_id_scope_allows_other_projects_and_sources(
    trace_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, _ = trace_client
    first = client.post(
        "/projects/project-1/traces", json=trace_payload(), headers=headers(settings)
    )
    other_project = client.post(
        "/projects/project-2/traces", json=trace_payload(), headers=headers(settings, "project-2")
    )
    other_source = trace_payload()
    other_source["source"] = "otlp"
    other_source_result = client.post(
        "/projects/project-1/traces", json=other_source, headers=headers(settings)
    )

    assert first.status_code == 201
    assert other_project.status_code == 201
    assert other_source_result.status_code == 201


def test_api_key_cannot_be_used_for_another_project(
    trace_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = trace_client
    raw_key, record = issue_project_key("project-1", settings)
    session.add(record)
    session.commit()

    response = client.post(
        "/projects/project-2/traces",
        json=trace_payload(),
        headers={"X-Project-Key": raw_key},
    )

    assert response.status_code == 401
    assert session.query(TraceRecord).count() == 0


def test_trace_limits_reject_before_any_record_is_persisted(
    trace_client: tuple[TestClient, Settings, Session],
) -> None:
    client, settings, session = trace_client

    settings.trace_max_request_bytes = 64
    too_large_request = client.post(
        "/projects/project-1/traces", json=trace_payload(), headers=headers(settings)
    )
    assert too_large_request.status_code == 413
    assert session.query(TraceRecord).count() == 0
    assert session.query(TraceSpanRecord).count() == 0

    settings.trace_max_request_bytes = 1_048_576
    settings.trace_max_spans = 2
    too_many_spans = client.post(
        "/projects/project-1/traces", json=trace_payload(), headers=headers(settings)
    )
    assert too_many_spans.status_code == 413
    assert session.query(TraceRecord).count() == 0
    assert session.query(TraceSpanRecord).count() == 0

    settings.trace_max_spans = 1_000
    settings.trace_max_nesting_depth = 1
    too_deep = client.post(
        "/projects/project-1/traces", json=trace_payload(), headers=headers(settings)
    )
    assert too_deep.status_code == 413
    assert session.query(TraceRecord).count() == 0
    assert session.query(TraceSpanRecord).count() == 0
