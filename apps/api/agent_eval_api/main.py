from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from starlette.middleware.base import RequestResponseEndpoint

from agent_eval_api.adapter_capabilities import router as adapter_capabilities_router
from agent_eval_api.agents import migrations_router
from agent_eval_api.agents import releases_router as agent_releases_router
from agent_eval_api.annotations import router as annotations_router
from agent_eval_api.auth import AuthContext, require_project_access
from agent_eval_api.comparisons import router as comparisons_router
from agent_eval_api.contracts import AccessCheckResponse, HealthResponse, SdkContractResponse
from agent_eval_api.credential_encryption import validate_runtime_credential_configuration
from agent_eval_api.datasets import router as datasets_router
from agent_eval_api.db import get_session_factory
from agent_eval_api.evaluation_runs import experiments_router
from agent_eval_api.evaluation_runs import router as evaluation_runs_router
from agent_eval_api.evaluator_connections import router as evaluator_connections_router
from agent_eval_api.evaluators import router as evaluators_router
from agent_eval_api.project_keys import router as project_keys_router
from agent_eval_api.provider_connections import router as provider_connections_router
from agent_eval_api.regression_gates import router as regression_gates_router
from agent_eval_api.remote_triggers import router as remote_triggers_router
from agent_eval_api.reports import router as reports_router
from agent_eval_api.settings import get_settings
from agent_eval_api.traces import router as traces_router
from agent_eval_api.users import router as users_router


def create_app() -> FastAPI:
    validate_runtime_credential_configuration(get_settings())
    app = FastAPI(
        title="Agent Eval Workbench API",
        version="0.1.0",
        description="Trace-driven evaluation for RAG, tool, and custom agents.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
            "http://localhost:3002",
            "http://127.0.0.1:3002",
            "http://localhost:13000",
            "http://127.0.0.1:13000",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def enforce_trace_request_limit(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method == "POST" and _is_trace_ingestion_path(request.url.path):
            settings_factory = app.dependency_overrides.get(get_settings, get_settings)
            settings = settings_factory()
            content_length = request.headers.get("content-length")
            if content_length is not None and int(content_length) > (
                settings.trace_max_request_bytes
            ):
                return JSONResponse(
                    status_code=413,
                    content={"detail": "trace request exceeds the configured size limit"},
                )
            body = await request.body()
            if len(body) > settings.trace_max_request_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "trace request exceeds the configured size limit"},
                )
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        _: object, exc: RequestValidationError
    ) -> JSONResponse:
        """Return validation metadata without echoing submitted field values."""

        safe_errors = [
            {
                "loc": list(error.get("loc", ())),
                "msg": error.get("msg", "Invalid request"),
                "type": error.get("type", "value_error"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": safe_errors})

    app.include_router(agent_releases_router)
    app.include_router(migrations_router)
    app.include_router(adapter_capabilities_router)
    app.include_router(annotations_router)
    app.include_router(datasets_router)
    app.include_router(comparisons_router)
    app.include_router(evaluators_router)
    app.include_router(evaluator_connections_router)
    app.include_router(evaluation_runs_router)
    app.include_router(experiments_router)
    app.include_router(reports_router)
    app.include_router(regression_gates_router)
    app.include_router(remote_triggers_router)
    app.include_router(traces_router)
    app.include_router(project_keys_router)
    app.include_router(provider_connections_router)
    app.include_router(users_router)

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        """Return process liveness without requiring backing services."""

        settings = get_settings()
        return HealthResponse(status="ok", environment=settings.app_env)

    @app.get("/ready", response_model=HealthResponse, tags=["system"])
    def readiness() -> HealthResponse:
        """Return readiness only when the configured database accepts queries."""

        settings = get_settings()
        session = None
        try:
            session = get_session_factory()()
            session.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        finally:
            if session is not None:
                session.close()
        return HealthResponse(status="ok", environment=settings.app_env)

    @app.get(
        "/projects/{project_id}/access-check",
        response_model=AccessCheckResponse,
        tags=["auth"],
    )
    def access_check(
        project_id: str,
        auth: AuthContext = Depends(require_project_access),  # noqa: B008
    ) -> AccessCheckResponse:
        return AccessCheckResponse(
            project_id=auth.project_id,
            principal_type=auth.principal_type,
        )

    @app.get(
        "/projects/{project_id}/sdk-contract",
        response_model=SdkContractResponse,
        tags=["system"],
    )
    def sdk_contract(
        project_id: str,
        _: AuthContext = Depends(require_project_access),  # noqa: B008
    ) -> SdkContractResponse:
        return SdkContractResponse(service_version=app.version)

    return app


def _is_trace_ingestion_path(path: str) -> bool:
    parts = path.strip("/").split("/")
    return len(parts) >= 3 and parts[0] == "projects" and parts[2] == "traces" and (
        len(parts) == 3 or parts[3] in {"ingest", "otlp"}
    )


app = create_app()
