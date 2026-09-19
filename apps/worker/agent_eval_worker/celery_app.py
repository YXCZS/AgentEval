from celery import Celery  # type: ignore[import-untyped]
from celery.signals import task_failure  # type: ignore[import-untyped]

from agent_eval_api.credential_encryption import validate_runtime_credential_configuration
from agent_eval_api.observability import configure as configure_observability
from agent_eval_api.observability import record_task_failure
from agent_eval_api.settings import get_settings

settings = get_settings()
validate_runtime_credential_configuration(settings)
configure_observability()
celery_app = Celery(
    "agent_eval",
    broker=settings.redis_url,
    backend=settings.redis_url,
    # The MVP worker must not import or register the legacy per-Case HTTP
    # Agent runner. SDK experiments execute in the caller's process.
    include=["agent_eval_worker.managed_tasks"],
)
celery_app.conf.update(
    task_default_queue="evaluation",
    task_routes={"agent_eval.execute_managed_judge": {"queue": "managed-judge"}},
    worker_concurrency=settings.worker_max_concurrency,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)


@task_failure.connect
def on_task_failure(sender=None, task_id=None, exception=None, **_kwargs):  # type: ignore[no-untyped-def]
    """Record a structured log entry whenever a task fails outright.

    This is the worker-side alert hook: operators can alert on
    ``worker_task_failed`` records (or their rising rate) without an external
    task monitor. Retried tasks surface only after their retries are exhausted,
    because a retry raises and is caught internally rather than failing the task.
    """
    name = getattr(sender, "name", None) or "unknown_task"
    failure = exception if exception is not None else RuntimeError("unknown failure")
    record_task_failure(str(name), str(task_id), failure)
