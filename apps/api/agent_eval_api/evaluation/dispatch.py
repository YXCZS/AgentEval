"""Celery producer for server-side evaluation jobs."""

from __future__ import annotations

from functools import lru_cache

from celery import Celery  # type: ignore[import-untyped]


@lru_cache
def _producer(redis_url: str) -> Celery:
    return Celery("agent_eval_api", broker=redis_url)


def dispatch_managed_judge(*, redis_url: str, score_id: str) -> str:
    """Send only a database identifier; credentials stay in encrypted storage."""

    result = _producer(redis_url).send_task(
        "agent_eval.execute_managed_judge",
        args=[score_id],
        queue="managed-judge",
    )
    return str(result.id)
