"""Celery tasks registered by the production evaluation worker."""

from __future__ import annotations

from typing import Any

from agent_eval_api.db import ScoreRecord, get_session_factory
from agent_eval_api.evaluation import ManagedJudgeRetryableError
from agent_eval_api.settings import get_settings

from .celery_app import celery_app
from .managed_judge import execute_managed_judge, fail_managed_judge


@celery_app.task(bind=True, name="agent_eval.execute_managed_judge", max_retries=None)  # type: ignore[misc]
def execute_managed_judge_task(task: Any, score_id: str) -> dict[str, Any]:
    settings = get_settings()
    session = get_session_factory()()
    try:
        try:
            return execute_managed_judge(
                session,
                settings,
                score_id=score_id,
                owner_id=str(task.request.id),
            )
        except ManagedJudgeRetryableError as exc:
            score = session.get(ScoreRecord, score_id)
            retries = 2
            backoff = 1.0
            if score is not None and score.run_id is not None:
                run = score.run
                snapshot: dict[str, Any] = {}
                if run is not None:
                    snapshot = next(
                        (
                            item
                            for item in run.configuration_snapshot.get("evaluators", [])
                            if str(item.get("id")) == score.evaluator_version_id
                        ),
                        {},
                    )
                config = snapshot.get("config") or {}
                retries = int(config.get("max_retries", retries))
                backoff = float(config.get("retry_backoff_seconds", backoff))
            if task.request.retries >= retries:
                return fail_managed_judge(
                    session,
                    settings,
                    score_id=score_id,
                    owner_id=f"terminal-{task.request.id}",
                    message="managed Judge provider remained unavailable after retries",
                )
            raise task.retry(
                exc=exc,
                countdown=backoff * (2**task.request.retries),
                max_retries=retries,
            ) from exc
    finally:
        session.close()
