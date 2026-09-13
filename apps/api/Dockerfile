FROM python:3.12-slim

WORKDIR /workspace
COPY pyproject.toml ./
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python -m agent_eval_api.bootstrap && uvicorn agent_eval_api.main:app --host 0.0.0.0 --port 8000"]
