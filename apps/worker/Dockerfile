FROM python:3.12-slim

WORKDIR /workspace
# 国内构建可通过 build arg 切换 PyPI 镜像源（compose 由 .env.production 注入）。
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY pyproject.toml ./
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" .

CMD ["sh", "-c", "alembic upgrade head && celery -A agent_eval_worker.celery_app:celery_app worker --loglevel=INFO --queues=evaluation,managed-judge"]
