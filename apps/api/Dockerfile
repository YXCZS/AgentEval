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

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python -m agent_eval_api.bootstrap && uvicorn agent_eval_api.main:app --host 0.0.0.0 --port 8000"]
