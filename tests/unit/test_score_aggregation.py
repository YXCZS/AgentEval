from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from agent_eval_api.db import (
    AggregateMetricRecord,
    Base,
    EvaluationRunRecord,
    ScoreRecord,
)
from agent_eval_api.evaluation import aggregate_run_scores


def score(
    score_id: str,
    *,
    evaluator_id: str = "evaluator-1",
    status: str,
    value: float | None = None,
    passed: bool | None = None,
) -> ScoreRecord:
    return ScoreRecord(
        id=score_id,
        run_id="run-1",
        case_id=score_id,
        metric_name="quality",
        evaluator_version_id=evaluator_id,
        status=status,
        value=value,
        passed=passed,
        evidence=[],
        threshold=0.8,
        direction="higher_is_better",
    )


def test_aggregate_tracks_valid_missing_error_and_evaluator_version_separately() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        run = EvaluationRunRecord(
            id="run-1",
            project_id="project-1",
            agent_version_id="agent-version-1",
            dataset_version_id="dataset-version-1",
            status="completed",
            configuration_snapshot={},
            total_cases=4,
        )
        session.add(run)
        session.add_all(
            [
                score("score-1", status="passed", value=0.9, passed=True),
                score("score-2", status="failed", value=0.5, passed=False),
                score("score-3", status="missing"),
                score("score-4", status="error"),
                score(
                    "score-5",
                    evaluator_id="evaluator-2",
                    status="passed",
                    value=1,
                    passed=True,
                ),
            ]
        )
        session.flush()

        first = aggregate_run_scores(session, run)
        second = aggregate_run_scores(session, run)

        assert len(first) == 2
        assert len(second) == 2
        records = session.scalars(
            select(AggregateMetricRecord).order_by(AggregateMetricRecord.evaluator_version_id)
        ).all()
        assert len(records) == 2
        primary = records[0]
        assert primary.valid_count == 2
        assert primary.missing_count == 1
        assert primary.error_count == 1
        assert primary.passed_count == 1
        assert primary.average == 0.7
        assert primary.pass_rate == 0.5
        assert primary.aggregation == "mean"
        assert records[1].valid_count == 1
        assert records[1].aggregation == "pass_rate"
        assert records[1].missing_count == 3


def test_aggregate_creates_missing_metric_when_no_score_was_persisted() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        run = EvaluationRunRecord(
            id="run-1",
            project_id="project-1",
            agent_version_id="agent-version-1",
            dataset_version_id="dataset-version-1",
            status="partial",
            configuration_snapshot={
                "evaluators": [
                    {
                        "id": "evaluator-1",
                        "name": "task_success",
                        "default_threshold": 1,
                        "direction": "higher_is_better",
                    }
                ]
            },
            total_cases=2,
        )
        session.add(run)
        session.flush()

        records = aggregate_run_scores(session, run)

        assert len(records) == 1
        assert records[0].metric_name == "task_success"
        assert records[0].valid_count == 0
        assert records[0].missing_count == 2
        assert records[0].passed_count == 0
        assert records[0].pass_rate is None
