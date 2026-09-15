"""Verify the production integration protocols against a running Compose stack.

This is an explicit live acceptance lane. It uses a real model-backed Tool Agent
for every successful protocol path and never supplies a mock or fallback result.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import httpx
from agent_eval import Client, DatasetCase, TaskResult
from agent_eval.telemetry import TelemetrySession
from dotenv import load_dotenv
from opentelemetry.trace import Span

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_eval_api.remote_trigger_protocol import verify_remote_trigger_delivery
from tests.live.provider_preflight import LiveProviderConfig, run_preflight
from tests.live.run_tool_acceptance import PlatformConfig
from tests.live.tool_agent import LiveToolAgent
from tests.live.tool_dataset import (
    TOOL_CASES,
    ensure_tool_acceptance_resources,
)


class ComposeAcceptanceError(RuntimeError):
    """The running Compose stack did not satisfy a production contract."""


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    expected: tuple[int, ...] = (200, 201),
) -> Any:
    response = client.request(method, path, json=payload)
    if response.status_code not in expected:
        raise ComposeAcceptanceError(
            f"{method} {path} returned HTTP {response.status_code}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ComposeAcceptanceError(f"{method} {path} returned invalid JSON") from exc


def _create_run(
    client: httpx.Client,
    *,
    project_id: str,
    name: str,
    dataset_version_id: str,
    release_id: str,
    evaluator_ids: list[str],
    execution_mode: str,
    evidence_policy: str = "tool_trajectory_required",
) -> dict[str, Any]:
    return _request(
        client,
        "POST",
        f"/projects/{project_id}/experiments",
        {
            "name": name,
            "agent_version_id": release_id,
            "dataset_version_id": dataset_version_id,
            "evaluator_version_ids": evaluator_ids,
            "execution_mode": execution_mode,
            "evidence_policy": evidence_policy,
            "execution_options": {
                "repetitions": 1,
                "concurrency": 1,
                "timeout_seconds": 60,
                "max_retries": 0,
                "retry_backoff_seconds": 0,
            },
        },
        expected=(201,),
    )


def _trace_attribute(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, (list, tuple)):
        return {
            "arrayValue": {
                "values": [_trace_attribute(item) for item in value],
            }
        }
    if isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=True, sort_keys=True)
    return {"stringValue": str(value)}


def _timestamp_nanos(value: str | None) -> str:
    if not value:
        return str(int(datetime.now().timestamp() * 1_000_000_000))
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return str(int(parsed.timestamp() * 1_000_000_000))


def _to_otlp(trace_payload: dict[str, Any]) -> dict[str, Any]:
    """Convert the SDK's captured real spans into an OTLP HTTP envelope."""

    otlp_spans: list[dict[str, Any]] = []
    for span in trace_payload.get("spans", []):
        if not isinstance(span, dict):
            continue
        kind = str(span.get("kind", "agent")).upper()
        attributes = dict(span.get("attributes") or {})
        attributes.setdefault("openinference.span.kind", kind)
        if span.get("input") is not None:
            attributes.setdefault(
                "agent_eval.input",
                json.dumps(span["input"], ensure_ascii=True, sort_keys=True),
            )
        if span.get("output") is not None:
            attributes.setdefault(
                "agent_eval.output",
                json.dumps(span["output"], ensure_ascii=True, sort_keys=True),
            )
        if kind == "TOOL":
            attributes.setdefault(
                "tool.call.arguments",
                json.dumps(span.get("input") or {}, ensure_ascii=True, sort_keys=True),
            )
        if kind == "TOOL_RESULT":
            attributes.setdefault(
                "tool.call.result",
                json.dumps(span.get("output") or {}, ensure_ascii=True, sort_keys=True),
            )
        usage = span.get("usage") or {}
        if isinstance(usage, dict):
            for source, target in (
                ("input_tokens", "gen_ai.usage.input_tokens"),
                ("output_tokens", "gen_ai.usage.output_tokens"),
                ("total_tokens", "gen_ai.usage.total_tokens"),
            ):
                if source in usage:
                    attributes.setdefault(target, usage[source])
        otlp_span = {
            "traceId": span["trace_id"],
            "spanId": span["span_id"],
            "name": span["name"],
            "startTimeUnixNano": _timestamp_nanos(span.get("started_at")),
            "endTimeUnixNano": _timestamp_nanos(span.get("ended_at")),
            "status": {
                "code": (
                    "STATUS_CODE_ERROR"
                    if span.get("status") == "failed"
                    else "STATUS_CODE_OK"
                )
            },
            "attributes": [
                {"key": key, "value": _trace_attribute(value)}
                for key, value in attributes.items()
            ],
        }
        if span.get("parent_span_id"):
            otlp_span["parentSpanId"] = span["parent_span_id"]
        otlp_spans.append(otlp_span)
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "compose-live-acceptance"},
                        }
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {
                            "name": "agent-eval-compose-acceptance",
                            "version": "1.0.0",
                        },
                        "spans": otlp_spans,
                    }
                ],
            }
        ]
    }


def _run_real_case(
    *,
    agent: LiveToolAgent,
    case: DatasetCase,
    experiment_id: str,
    item_id: str,
    release_identity: str,
    origin: str,
) -> tuple[TaskResult, dict[str, Any]]:
    telemetry = TelemetrySession()
    root: Span | None = None
    with telemetry.tracer.start_as_current_span("compose.acceptance.task") as span:
        root = span
        span.set_attribute("openinference.span.kind", "AGENT")
        span.set_attribute("agent_eval.project.id", os.environ["AGENT_EVAL_PROJECT_ID"])
        span.set_attribute("agent_eval.experiment.id", experiment_id)
        span.set_attribute("agent_eval.experiment.item.id", item_id)
        span.set_attribute("agent_eval.dataset.id", case.metadata["dataset_id"])
        span.set_attribute(
            "agent_eval.dataset.version.id",
            case.metadata["dataset_version_id"],
        )
        span.set_attribute("agent_eval.case.id", case.id)
        span.set_attribute("agent_eval.agent.release", release_identity)
        span.set_attribute("agent_eval.execution.origin", origin)
        span.set_attribute("agent_eval.repetition", 1)
        result = agent.run(case)
    if root is None or not telemetry.force_flush():
        raise ComposeAcceptanceError("real Agent telemetry flush failed")
    trace_payload = telemetry.trace_payload(
        root_span=root,
        experiment_id=experiment_id,
        case_id=case.id,
    )
    trace_payload["source"] = "otlp"
    return result, trace_payload


def _execute_protocol_case(
    client: httpx.Client,
    *,
    project_id: str,
    run: dict[str, Any],
    case: DatasetCase,
    agent: LiveToolAgent,
    origin: str,
    release_identity: str,
) -> dict[str, Any]:
    item = _request(
        client,
        "POST",
        f"/projects/{project_id}/experiments/{run['id']}/items/start",
        {
            "case_id": case.id,
            "repetition": 1,
            "attempt": 1,
            "external_run_id": f"compose-{run['id']}-{case.id}",
            "expected_status": "queued",
            "runtime_metadata": {"runtime": "compose-live-acceptance"},
        },
        expected=(201,),
    )
    case.metadata = {
        **case.metadata,
        "dataset_id": run["configuration_snapshot"]["dataset_version"]["dataset_id"],
        "dataset_version_id": run["dataset_version_id"],
    }
    result, trace_payload = _run_real_case(
        agent=agent,
        case=case,
        experiment_id=run["id"],
        item_id=item["id"],
        release_identity=release_identity,
        origin=origin,
    )
    trace_response = _request(
        client,
        "POST",
        f"/projects/{project_id}/traces/otlp",
        _to_otlp(trace_payload),
        expected=(201,),
    )
    completed = _request(
        client,
        "POST",
        f"/projects/{project_id}/experiments/{run['id']}/items/{item['id']}/complete",
        {
            "expected_status": "running",
            "output": result.output,
            "usage": result.usage,
            "runtime_metadata": result.metadata,
            "trace_id": trace_response["trace_id"],
        },
        expected=(200,),
    )
    return completed


def _release(client: Client, label: str) -> Any:
    return client.register_release(
        label=label,
        agent_type="tool",
        release_identity=f"compose-live:{label}",
        source_revision=f"compose-live:{label}",
        metadata={"acceptance_kind": "real_compose_protocol"},
    )


def _single_case_dataset(
    http: httpx.Client,
    project_id: str,
    *,
    name: str,
    case: dict[str, Any],
) -> tuple[str, str]:
    created = _request(
        http,
        "POST",
        f"/projects/{project_id}/datasets",
        {
            "name": name,
            "description": "Temporary live protocol acceptance Dataset",
            "tags": ["live-acceptance", "compose"],
            "cases": [case],
            "metadata": {"acceptance_kind": "real_compose_protocol"},
        },
        expected=(201,),
    )
    return str(created["id"]), str(created["current_version_id"])


def _run_single_mode(
    *,
    platform: PlatformConfig,
    provider: LiveProviderConfig,
    mode: str,
    evaluator_ids: list[str],
    dataset_version_id: str,
) -> str:
    with Client(
        base_url=platform.base_url,
        project_id=platform.project_id,
        api_key=platform.api_key,
    ) as sdk, httpx.Client(
        base_url=platform.base_url,
        headers={"X-Project-Key": platform.api_key},
        timeout=60,
    ) as http:
        release = _release(sdk, f"{mode}-{os.getpid()}")
        run = _create_run(
            http,
            project_id=platform.project_id,
            name=f"Compose {mode} acceptance",
            dataset_version_id=dataset_version_id,
            release_id=release.id,
            evaluator_ids=evaluator_ids,
            execution_mode=mode,
        )
        manifest = _request(
            http,
            "GET",
            f"/projects/{platform.project_id}/experiments/{run['id']}/manifest",
        )
        case = DatasetCase.model_validate(manifest["items"][0]["case"])
        _execute_protocol_case(
            http,
            project_id=platform.project_id,
            run=run,
            case=case,
            agent=LiveToolAgent(provider),
            origin=mode,
            release_identity=release.release_identity,
        )
        finalized = _request(
            http,
            "POST",
            f"/projects/{platform.project_id}/experiments/{run['id']}/finalize",
            expected=(200,),
        )
        if finalized["status"] != "completed":
            raise ComposeAcceptanceError(f"{mode} Experiment did not complete")
        return str(run["id"])


def _run_failure_isolation(
    *,
    platform: PlatformConfig,
    provider: LiveProviderConfig,
    evaluator_ids: list[str],
) -> str:
    with Client(
        base_url=platform.base_url,
        project_id=platform.project_id,
        api_key=platform.api_key,
    ) as sdk, httpx.Client(
        base_url=platform.base_url,
        headers={"X-Project-Key": platform.api_key},
        timeout=60,
    ) as http:
        dataset_id, version_id = _single_case_dataset(
            http,
            platform.project_id,
            name=f"Compose failure isolation {os.getpid()}",
            case=TOOL_CASES[0],
        )
        second = dict(TOOL_CASES[1])
        _request(
            http,
            "POST",
            f"/projects/{platform.project_id}/datasets/{dataset_id}/versions",
            {
                "cases": [TOOL_CASES[0], second],
                "metadata": {"acceptance_kind": "real_compose_protocol"},
            },
            expected=(201,),
        )
        versions = _request(
            http,
            "GET",
            f"/projects/{platform.project_id}/datasets/{dataset_id}/versions",
        )
        version_id = str(versions[-1]["id"])
        release = _release(sdk, f"failure-isolation-{os.getpid()}")
        run = _create_run(
            http,
            project_id=platform.project_id,
            name="Compose single Case failure isolation",
            dataset_version_id=version_id,
            release_id=release.id,
            evaluator_ids=evaluator_ids,
            execution_mode="remote_upload",
        )
        manifest = _request(
            http,
            "GET",
            f"/projects/{platform.project_id}/experiments/{run['id']}/manifest",
        )
        first_case = DatasetCase.model_validate(manifest["items"][0]["case"])
        second_case = DatasetCase.model_validate(manifest["items"][1]["case"])
        _execute_protocol_case(
            http,
            project_id=platform.project_id,
            run=run,
            case=first_case,
            agent=LiveToolAgent(provider),
            origin="remote_upload",
            release_identity=release.release_identity,
        )
        failed_item = _request(
            http,
            "POST",
            f"/projects/{platform.project_id}/experiments/{run['id']}/items/start",
            {
                "case_id": second_case.id,
                "repetition": 1,
                "attempt": 1,
                "external_run_id": f"compose-failure-{run['id']}",
                "expected_status": "queued",
                "runtime_metadata": {"runtime": "compose-live-acceptance"},
            },
            expected=(201,),
        )
        _request(
            http,
            "POST",
            f"/projects/{platform.project_id}/experiments/{run['id']}/items/{failed_item['id']}/fail",
            {
                "expected_status": "running",
                "error_type": "intentional_acceptance_failure",
                "error_message": "one Case failed while another completed",
                "runtime_metadata": {"runtime": "compose-live-acceptance"},
            },
            expected=(200,),
        )
        finalized = _request(
            http,
            "POST",
            f"/projects/{platform.project_id}/experiments/{run['id']}/finalize",
            expected=(200,),
        )
        if (
            finalized["status"] != "partial"
            or finalized["completed_cases"] != 1
            or finalized["failed_cases"] != 1
        ):
            raise ComposeAcceptanceError("single Case failure was not isolated")
        return str(run["id"])


def _run_remote_trigger(
    *,
    platform: PlatformConfig,
    provider: LiveProviderConfig,
    evaluator_ids: list[str],
    dataset_version_id: str,
    release: Any,
) -> str:
    session_secret = os.getenv("WORKSPACE_SESSION_SECRET", "").strip()
    if not session_secret:
        raise ComposeAcceptanceError("WORKSPACE_SESSION_SECRET is required for Trigger setup")
    trigger_secret: str | None = None
    receiver_error: list[Exception] = []

    def handle_delivery(headers: dict[str, str], body: bytes) -> None:
        if trigger_secret is None:
            raise ComposeAcceptanceError("Trigger secret was not initialized")
        payload = verify_remote_trigger_delivery(
            headers,
            body,
            signing_secret=trigger_secret,
        )
        callback = payload["callback"]
        with httpx.Client(
            base_url=callback["api_base_url"],
            headers={"X-Project-Key": platform.api_key},
            timeout=60,
        ) as callback_http:
            manifest = _request(
                callback_http,
                "GET",
                f"/projects/{platform.project_id}/experiments/"
                f"{payload['experiment']['id']}/manifest",
            )
            case = DatasetCase.model_validate(manifest["items"][0]["case"])
            run = _request(
                callback_http,
                "GET",
                f"/projects/{platform.project_id}/experiments/"
                f"{payload['experiment']['id']}",
            )
            _execute_protocol_case(
                callback_http,
                project_id=platform.project_id,
                run=run,
                case=case,
                agent=LiveToolAgent(provider),
                origin="remote_upload",
                release_identity=release.release_identity,
            )
            finalized = _request(
                callback_http,
                "POST",
                f"/projects/{platform.project_id}/experiments/"
                f"{payload['experiment']['id']}/finalize",
                expected=(200,),
            )
            if finalized["status"] != "completed":
                raise ComposeAcceptanceError("Trigger callback Experiment did not complete")

    class TriggerHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            try:
                handle_delivery(dict(self.headers.items()), body)
                self.send_response(202)
                self.end_headers()
            except Exception as exc:  # pragma: no cover - exercised by live lane
                receiver_error.append(exc)
                self.send_response(500)
                self.end_headers()

        def log_message(self, *_: Any) -> None:
            return

    server = ThreadingHTTPServer(("0.0.0.0", 0), TriggerHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=platform.base_url,
            headers={"X-Project-Key": platform.api_key},
            timeout=60,
        ) as http:
            trigger_dataset_id, trigger_version_id = _single_case_dataset(
                http,
                platform.project_id,
                name=f"Compose Remote Trigger {os.getpid()}",
                case=TOOL_CASES[0],
            )
            trigger_created = _request(
                http,
                "POST",
                f"/projects/{platform.project_id}/datasets/{trigger_dataset_id}/remote-trigger",
                {"trigger_url": f"http://host.docker.internal:{server.server_port}/trigger"},
                expected=(403,),
            )
            del trigger_created
        browser_headers = {
            "X-Workspace-Session": f"dev:{platform.project_id}:{session_secret}"
        }
        with httpx.Client(
            base_url=platform.base_url,
            headers=browser_headers,
            timeout=60,
        ) as browser:
            trigger_created = _request(
                browser,
                "POST",
                f"/projects/{platform.project_id}/datasets/{trigger_dataset_id}/remote-trigger",
                {"trigger_url": f"http://host.docker.internal:{server.server_port}/trigger"},
                expected=(201,),
            )
        trigger_secret = str(trigger_created["signing_secret"])
        with httpx.Client(
            base_url=platform.base_url,
            headers={"X-Project-Key": platform.api_key},
            timeout=120,
        ) as http:
            run = _create_run(
                http,
                project_id=platform.project_id,
                name="Compose Remote Trigger acceptance",
                dataset_version_id=trigger_version_id,
                release_id=release.id,
                evaluator_ids=evaluator_ids,
                execution_mode="remote_trigger",
            )
            run_id = str(run["id"])
            persisted = _request(
                http,
                "GET",
                f"/projects/{platform.project_id}/experiments/{run_id}",
            )
            deliveries = _request(
                http,
                "GET",
                f"/projects/{platform.project_id}/datasets/{trigger_dataset_id}/remote-trigger/deliveries",
            )
            if (
                persisted["status"] != "completed"
                or not deliveries
                or deliveries[0]["status"] != "accepted"
            ):
                raise ComposeAcceptanceError("Remote Trigger did not complete its callback run")
            if receiver_error:
                raise ComposeAcceptanceError(
                    "Remote Trigger receiver failed"
                ) from receiver_error[0]
            return run_id
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main() -> int:
    load_dotenv(override=False)
    try:
        platform = PlatformConfig.from_environment()
        provider = LiveProviderConfig.from_environment()
        run_preflight(provider)
        with httpx.Client(
            base_url=platform.base_url,
            headers={"X-Project-Key": platform.api_key},
            timeout=60,
        ) as http:
            resources = ensure_tool_acceptance_resources(http, platform.project_id)
            _, protocol_dataset_version_id = _single_case_dataset(
                http,
                platform.project_id,
                name=f"Compose protocol modes {os.getpid()}",
                case=TOOL_CASES[0],
            )
        mode_runs = {
            mode: _run_single_mode(
                platform=platform,
                provider=provider,
                mode=mode,
                evaluator_ids=list(resources.evaluator_version_ids),
                dataset_version_id=protocol_dataset_version_id,
            )
            for mode in ("otel", "remote_upload")
        }
        isolation_run = _run_failure_isolation(
            platform=platform,
            provider=provider,
            evaluator_ids=list(resources.evaluator_version_ids),
        )
        with Client(
            base_url=platform.base_url,
            project_id=platform.project_id,
            api_key=platform.api_key,
        ) as sdk:
            release = _release(sdk, f"remote-trigger-{os.getpid()}")
        trigger_run = _run_remote_trigger(
            platform=platform,
            provider=provider,
            evaluator_ids=list(resources.evaluator_version_ids),
            dataset_version_id=protocol_dataset_version_id,
            release=release,
        )
        print(
            json.dumps(
                {
                    "accepted": True,
                    "modes": mode_runs,
                    "failure_isolation_experiment": isolation_run,
                    "remote_trigger_experiment": trigger_run,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        print(f"compose live acceptance failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
