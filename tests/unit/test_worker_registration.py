"""Verify the production Celery worker's registered task and delivery policy."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_eval_api.evaluation import ManagedJudgeRetryableError
from agent_eval_worker.celery_app import celery_app, settings
from agent_eval_worker.managed_tasks import execute_managed_judge_task


def test_legacy_http_task_is_not_registered_by_production_worker() -> None:
    celery_app.loader.import_default_modules()

    assert "agent_eval.execute_case" not in celery_app.tasks


def test_managed_judge_task_uses_a_bounded_reliable_dedicated_queue() -> None:
    celery_app.loader.import_default_modules()

    assert "agent_eval.execute_managed_judge" in celery_app.tasks
    assert celery_app.conf.task_routes == {
        "agent_eval.execute_managed_judge": {"queue": "managed-judge"}
    }
    assert celery_app.conf.worker_concurrency == settings.worker_max_concurrency
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True


def test_worker_image_consumes_the_managed_judge_queue() -> None:
    dockerfile = Path(__file__).resolve().parents[2] / "apps" / "worker" / "Dockerfile"

    assert "--queues=evaluation,managed-judge" in dockerfile.read_text(encoding="utf-8")


class FakeSession:
    def __init__(self, *, retries: int, backoff: float) -> None:
        run = SimpleNamespace(
            configuration_snapshot={
                "evaluators": [
                    {
                        "id": "evaluator-1",
                        "config": {
                            "max_retries": retries,
                            "retry_backoff_seconds": backoff,
                        },
                    }
                ]
            }
        )
        self.score = SimpleNamespace(
            run_id="run-1",
            evaluator_version_id="evaluator-1",
            run=run,
        )
        self.closed = False

    def get(self, _model: object, score_id: str) -> object | None:
        return self.score if score_id == "score-1" else None

    def close(self) -> None:
        self.closed = True


def _install_retryable_task_boundary(
    monkeypatch: pytest.MonkeyPatch,
    session: FakeSession,
) -> None:
    monkeypatch.setattr(
        "agent_eval_worker.managed_tasks.get_session_factory",
        lambda: lambda: session,
    )
    monkeypatch.setattr(
        "agent_eval_worker.managed_tasks.execute_managed_judge",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ManagedJudgeRetryableError("temporary provider failure")
        ),
    )


def test_managed_task_uses_frozen_retry_limit_and_exponential_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession(retries=3, backoff=0.5)
    _install_retryable_task_boundary(monkeypatch, session)
    retry_call: dict[str, Any] = {}

    def retry(**kwargs: Any) -> RuntimeError:
        retry_call.update(kwargs)
        return RuntimeError("scheduled retry")

    monkeypatch.setattr(execute_managed_judge_task, "retry", retry)

    with pytest.raises(RuntimeError, match="scheduled retry"):
        execute_managed_judge_task.run("score-1")

    assert isinstance(retry_call["exc"], ManagedJudgeRetryableError)
    assert retry_call["countdown"] == 0.5
    assert retry_call["max_retries"] == 3
    assert session.closed is True


def test_managed_task_persists_terminal_error_after_retry_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession(retries=0, backoff=0.5)
    _install_retryable_task_boundary(monkeypatch, session)
    failures: list[dict[str, object]] = []

    def fail(*_args: object, **kwargs: object) -> dict[str, str]:
        failures.append(kwargs)
        return {"status": "error", "score_id": str(kwargs["score_id"])}

    monkeypatch.setattr("agent_eval_worker.managed_tasks.fail_managed_judge", fail)

    result = execute_managed_judge_task.run("score-1")

    assert result == {"status": "error", "score_id": "score-1"}
    assert failures == [
        {
            "score_id": "score-1",
            "owner_id": "terminal-None",
            "message": "managed Judge provider remained unavailable after retries",
        }
    ]
    assert session.closed is True
