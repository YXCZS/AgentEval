FROM python:3.12-slim

WORKDIR /workspace
COPY pyproject.toml ./
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
RUN pip install --no-cache-dir .

CMD ["sh", "-c", "alembic upgrade head && celery -A agent_eval_worker.celery_app:celery_app worker --loglevel=INFO --queues=evaluation,managed-judge"]
