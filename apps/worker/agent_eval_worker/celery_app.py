from celery import Celery  # type: ignore[import-untyped]

from agent_eval_api.credential_encryption import validate_runtime_credential_configuration
from agent_eval_api.settings import get_settings

settings = get_settings()
validate_runtime_credential_configuration(settings)
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
