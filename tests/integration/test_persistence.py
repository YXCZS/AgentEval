from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent_eval_api.db import (
    AgentRecord,
    AgentVersionRecord,
    Base,
    CaseExecutionRecord,
    DatasetCaseRecord,
    DatasetRecord,
    DatasetVersionRecord,
    EvaluationRunRecord,
    ExperimentItemAttemptRecord,
    ProjectRecord,
    TraceRecord,
    TraceSpanRecord,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def make_graph(
    session: Session,
) -> tuple[AgentVersionRecord, DatasetCaseRecord, EvaluationRunRecord]:
    project = ProjectRecord(id="project-1", name="demo")
    agent = AgentRecord(id="agent-1", project=project, name="Order agent", agent_type="tool")
    agent_version = AgentVersionRecord(
        id="agent-version-1",
        agent=agent,
        project=project,
        version=1,
        label="v1",
        agent_type="tool",
        release_identity="git-sha-v1",
        endpoint_config={"url": "https://agent.example.test/run"},
    )
    dataset = DatasetRecord(id="dataset-1", project=project, name="orders")
    dataset_version = DatasetVersionRecord(id="dataset-version-1", dataset=dataset, version=1)
    case = DatasetCaseRecord(
        id="case-record-1",
        dataset_version=dataset_version,
        case_key="case-1",
        input_json={"order_id": "42"},
        expected_tools=[{"name": "search_order"}],
    )
    run = EvaluationRunRecord(
        id="run-1",
        project_id=project.id,
        agent_version=agent_version,
        dataset_version=dataset_version,
        total_cases=1,
    )
    session.add_all([project, agent, dataset, run, case])
    session.commit()
    return agent_version, case, run


def test_version_relationships_and_json_fields_are_persisted(session: Session) -> None:
    agent_version, case, run = make_graph(session)

    execution = CaseExecutionRecord(id="execution-1", run=run, dataset_case=case)
    session.add(execution)
    trace = TraceRecord(
        id="trace-1",
        project_id=run.project_id,
        run=run,
        case_id=case.case_key,
        status="completed",
    )
    span = TraceSpanRecord(
        id="span-record-1",
        trace=trace,
        span_id="span-1",
        kind="tool",
        name="search_order",
        status="completed",
        started_at=datetime.now(UTC),
        attributes={"tool.call.arguments": {"order_id": "42"}},
    )
    execution.trace = trace
    session.add_all([execution, trace, span])
    session.commit()

    loaded = session.scalar(
        select(CaseExecutionRecord).where(CaseExecutionRecord.id == execution.id)
    )
    assert loaded is not None
    assert loaded.trace_id == "trace-1"
    assert loaded.dataset_case.expected_tools[0]["name"] == "search_order"
    assert loaded.run.agent_version_id == agent_version.id
    assert loaded.trace.spans[0].attributes["tool.call.arguments"]["order_id"] == "42"


def test_idempotency_and_unique_version_constraints_are_enforced(session: Session) -> None:
    _, case, run = make_graph(session)
    session.add(CaseExecutionRecord(id="execution-1", run=run, dataset_case=case))
    session.commit()

    session.add(CaseExecutionRecord(id="execution-2", run=run, dataset_case=case))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    duplicate_version = AgentVersionRecord(
        id="agent-version-2",
        project_id="project-1",
        agent_id="agent-1",
        version=1,
        label="same-v1",
        agent_type="tool",
        release_identity="git-sha-v1-duplicate",
        endpoint_config={"url": "https://agent.example.test/run"},
    )
    session.add(duplicate_version)
    with pytest.raises(IntegrityError):
        session.commit()


def test_versioned_history_cannot_be_updated(session: Session) -> None:
    agent_version, _, _ = make_graph(session)
    agent_version.label = "mutated"

    with pytest.raises(ValueError, match="immutable"):
        session.commit()


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [("execution_mode", "demo"), ("evidence_policy", "mock_required")],
)
def test_database_rejects_removed_experiment_contract_values(
    session: Session,
    field: str,
    invalid_value: str,
) -> None:
    _, _, run = make_graph(session)
    invalid_run = EvaluationRunRecord(
        id=f"invalid-{field}",
        project_id=run.project_id,
        agent_version_id=run.agent_version_id,
        dataset_version_id=run.dataset_version_id,
        execution_mode="sdk_task",
        evidence_policy="trace_required",
    )
    setattr(invalid_run, field, invalid_value)
    session.add(invalid_run)

    with pytest.raises(IntegrityError):
        session.commit()


def test_experiment_item_attempts_have_stable_unique_identities(session: Session) -> None:
    _, case, run = make_graph(session)
    first = ExperimentItemAttemptRecord(
        id="item-attempt-1",
        experiment=run,
        dataset_case=case,
        repetition=1,
        attempt=1,
        external_run_id="client-run-1",
        status="running",
        runtime_metadata={"sdk_version": "0.1.0", "python": "3.12"},
    )
    second_repetition = ExperimentItemAttemptRecord(
        id="item-attempt-2",
        experiment=run,
        dataset_case=case,
        repetition=2,
        attempt=1,
        external_run_id="client-run-2",
        status="running",
    )
    session.add_all([first, second_repetition])
    session.commit()

    assert first.runtime_metadata["sdk_version"] == "0.1.0"
    assert second_repetition.repetition == 2

    duplicate_position = ExperimentItemAttemptRecord(
        id="duplicate-position",
        experiment_id=run.id,
        case_id=case.id,
        repetition=1,
        attempt=1,
        external_run_id="client-run-3",
        status="queued",
    )
    session.add(duplicate_position)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    duplicate_external_id = ExperimentItemAttemptRecord(
        id="duplicate-external",
        experiment_id=run.id,
        case_id=case.id,
        repetition=3,
        attempt=1,
        external_run_id="client-run-1",
        status="queued",
    )
    session.add(duplicate_external_id)
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [("repetition", 0), ("attempt", 0), ("status", "unknown")],
)
def test_experiment_item_attempt_database_constraints_reject_invalid_values(
    session: Session,
    field: str,
    invalid_value: int | str,
) -> None:
    _, case, run = make_graph(session)
    item = ExperimentItemAttemptRecord(
        id=f"invalid-{field}",
        experiment=run,
        dataset_case=case,
        repetition=1,
        attempt=1,
        external_run_id=f"invalid-{field}",
        status="queued",
    )
    setattr(item, field, invalid_value)
    session.add(item)

    with pytest.raises(IntegrityError):
        session.commit()


def test_experiment_item_attempt_persists_runtime_evidence(session: Session) -> None:
    _, case, run = make_graph(session)
    started_at = datetime.now(UTC)
    finished_at = datetime.now(UTC)
    trace = TraceRecord(
        id="attempt-trace-1",
        project_id=run.project_id,
        run=run,
        case_id=case.case_key,
        status="failed",
    )
    item = ExperimentItemAttemptRecord(
        id="failed-item",
        experiment=run,
        dataset_case=case,
        repetition=1,
        attempt=2,
        external_run_id="client-failed-1",
        status="failed",
        output={"partial_answer": "real result before failure"},
        usage={"input_tokens": 12, "output_tokens": 4},
        runtime_metadata={"runtime": "user-process", "sdk_version": "0.1.0"},
        error_type="AgentRuntimeError",
        error_message="real agent call failed",
        trace=trace,
        started_at=started_at,
        finished_at=finished_at,
    )
    session.add_all([trace, item])
    session.commit()
    session.expunge_all()

    loaded = session.get(ExperimentItemAttemptRecord, "failed-item")
    assert loaded is not None
    assert loaded.experiment_id == "run-1"
    assert loaded.case_id == "case-record-1"
    assert loaded.repetition == 1
    assert loaded.attempt == 2
    assert loaded.external_run_id == "client-failed-1"
    assert loaded.output == {"partial_answer": "real result before failure"}
    assert loaded.error_type == "AgentRuntimeError"
    assert loaded.error_message == "real agent call failed"
    assert loaded.trace_id == "attempt-trace-1"
    assert loaded.usage == {"input_tokens": 12, "output_tokens": 4}
    assert loaded.runtime_metadata == {
        "runtime": "user-process",
        "sdk_version": "0.1.0",
    }
    assert loaded.created_at is not None
    assert loaded.started_at == started_at.replace(tzinfo=None)
    assert loaded.finished_at == finished_at.replace(tzinfo=None)


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
def test_terminal_experiment_item_evidence_is_immutable(
    session: Session,
    terminal_status: str,
) -> None:
    _, case, run = make_graph(session)
    item = ExperimentItemAttemptRecord(
        id=f"{terminal_status}-item",
        experiment=run,
        dataset_case=case,
        repetition=1,
        attempt=1,
        external_run_id=f"client-{terminal_status}-1",
        status=terminal_status,
        output={"answer": "real result"},
        usage={"input_tokens": 12, "output_tokens": 4},
        runtime_metadata={"runtime": "user-process"},
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    session.add(item)
    session.commit()

    item.output = {"answer": "rewritten result"}
    with pytest.raises(ValueError, match="immutable"):
        session.commit()


def test_experiment_definition_snapshot_is_immutable(session: Session) -> None:
    _, _, run = make_graph(session)
    run.configuration_snapshot = {"dataset_version": {"id": "rewritten"}}
    with pytest.raises(ValueError, match="experiment definitions are immutable"):
        session.commit()
