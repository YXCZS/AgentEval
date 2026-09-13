from __future__ import annotations

import os
import subprocess
import sys
from contextlib import AbstractContextManager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from tests.live import run_tool_acceptance as acceptance
from tests.live.provider_preflight import (
    LiveProviderConfig,
    LiveProviderEvidence,
    LiveProviderPreflightError,
)
from tests.live.tool_dataset import ToolAcceptanceResources


def platform_config() -> acceptance.PlatformConfig:
    return acceptance.PlatformConfig(
        base_url="http://platform.example.test",
        project_id="project-1",
        api_key="platform-test-secret",
    )


def provider_config() -> LiveProviderConfig:
    return LiveProviderConfig(
        base_url="https://provider.example.test/v1",
        api_key="provider-test-secret",
        model="configured-model",
        timeout_seconds=5,
    )


def preflight_evidence() -> LiveProviderEvidence:
    return LiveProviderEvidence(
        endpoint_origin="https://provider.example.test",
        configured_model="configured-model",
        response_model="provider-model-release",
        upstream_request_id="chatcmpl-live-preflight",
        input_tokens=10,
        output_tokens=2,
        total_tokens=12,
        challenge_verified=True,
    )


class ContextValue(AbstractContextManager[Any]):
    def __init__(self, value: Any) -> None:
        self.value = value

    def __enter__(self) -> Any:
        return self.value

    def __exit__(self, *_: object) -> None:
        return None


@pytest.mark.parametrize(
    "base_url",
    [
        "platform.example.test",
        "ftp://platform.example.test",
        "https://user:pass@platform.example.test",
        "https://platform.example.test:invalid",
    ],
)
def test_platform_environment_rejects_unsafe_urls(
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
) -> None:
    monkeypatch.setenv("AGENT_EVAL_BASE_URL", base_url)
    monkeypatch.setenv("AGENT_EVAL_PROJECT_ID", "project-1")
    monkeypatch.setenv("AGENT_EVAL_API_KEY", "platform-test-secret")

    with pytest.raises(acceptance.LiveAcceptanceConfigurationError):
        acceptance.PlatformConfig.from_environment()


def test_cli_script_runs_directly_and_reports_missing_configuration() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHON_DOTENV_DISABLED"] = "1"
    for name in (
        "AGENT_EVAL_BASE_URL",
        "AGENT_EVAL_PROJECT_ID",
        "AGENT_EVAL_API_KEY",
        "LIVE_ACCEPTANCE_BASE_URL",
        "LIVE_ACCEPTANCE_API_KEY",
        "LIVE_ACCEPTANCE_CHAT_MODEL",
    ):
        environment.pop(name, None)

    result = subprocess.run(
        [sys.executable, "tests/live/run_tool_acceptance.py"],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "live acceptance configuration error" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def test_provider_preflight_happens_before_platform_data_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[str] = []

    def fail_preflight(_: LiveProviderConfig) -> LiveProviderEvidence:
        raise LiveProviderPreflightError("provider is unavailable")

    monkeypatch.setattr(acceptance, "run_preflight", fail_preflight)
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **_: constructed.append("platform-http"),
    )
    monkeypatch.setattr(
        acceptance,
        "Client",
        lambda **_: constructed.append("sdk-client"),
    )

    with pytest.raises(LiveProviderPreflightError):
        acceptance.run_tool_acceptance(platform_config(), provider_config())

    assert constructed == []


def test_acceptance_uses_one_dataset_version_and_persisted_run_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = ToolAcceptanceResources(
        dataset_id="dataset-live",
        dataset_version_id="dataset-version-frozen",
        evaluator_version_ids=("eval-tool", "eval-args", "eval-state"),
        manifest_sha256="manifest-hash",
    )
    platform_http = object()
    sdk = object()
    release_calls: list[dict[str, Any]] = []
    run_calls: list[dict[str, Any]] = []
    post_calls: list[tuple[str, dict[str, Any]]] = []
    get_calls: list[str] = []

    monkeypatch.setattr(acceptance, "run_preflight", lambda _: preflight_evidence())
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **_: ContextValue(platform_http),
    )
    monkeypatch.setattr(acceptance, "Client", lambda **_: ContextValue(sdk))
    monkeypatch.setattr(
        acceptance,
        "ensure_tool_acceptance_resources",
        lambda client, project_id: resources,
    )

    def register_release(_client: object, **kwargs: Any) -> Any:
        release_calls.append(kwargs)
        return SimpleNamespace(id=f"release-{kwargs['variant']}")

    def run_release(_client: object, **kwargs: Any) -> Any:
        run_calls.append(kwargs)
        is_candidate = kwargs.get("baseline_experiment_id") is not None
        experiment_id = "experiment-candidate" if is_candidate else "experiment-baseline"
        return SimpleNamespace(
            experiment=SimpleNamespace(
                id=experiment_id,
                status="completed",
                dataset_version_id=resources.dataset_version_id,
                baseline_run_id=("experiment-baseline" if is_candidate else None),
            ),
            items=[
                SimpleNamespace(
                    status="completed",
                    evidence_status="complete",
                    trace_id=f"trace-{'candidate' if is_candidate else 'baseline'}-{index}",
                )
                for index in range(3)
            ],
        )

    def post_json(_client: object, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        post_calls.append((path, payload))
        if path.endswith("/comparisons"):
            return {
                "baseline_run_id": "experiment-baseline",
                "metric_comparisons": [{"metric_name": "live_business_state"}],
                "new_failures": [],
                "recovered_cases": [],
                "missing_evidence": [],
            }
        return {"run_id": "experiment-candidate", "status": "passed", "rules": []}

    monkeypatch.setattr(acceptance, "_register_release", register_release)
    monkeypatch.setattr(acceptance, "_run_release", run_release)
    monkeypatch.setattr(acceptance, "_post_json", post_json)

    def get_json(_client: object, path: str) -> dict[str, Any]:
        get_calls.append(path)
        return {"id": path}

    monkeypatch.setattr(acceptance, "_get_json", get_json)

    result = acceptance.run_tool_acceptance(platform_config(), provider_config())

    assert result.accepted is True
    assert [call["variant"] for call in release_calls] == ["baseline", "candidate"]
    assert [call["resources"].dataset_version_id for call in run_calls] == [
        "dataset-version-frozen",
        "dataset-version-frozen",
    ]
    assert run_calls[0].get("baseline_experiment_id") is None
    assert run_calls[1]["baseline_experiment_id"] == "experiment-baseline"
    assert post_calls[0][1] == {
        "run_ids": ["experiment-baseline", "experiment-candidate"]
    }
    assert post_calls[1][0].endswith(
        "/runs/experiment-candidate/regression-gate"
    )
    assert result.experiments["evidence_complete"] is True
    assert len(get_calls) == 9
    assert get_calls[:3] == [
        "/projects/project-1/datasets/dataset-live/versions/dataset-version-frozen",
        "/projects/project-1/experiments/experiment-baseline",
        "/projects/project-1/experiments/experiment-candidate",
    ]
    assert result.verified_resources["trace_ids"] == [
        "trace-baseline-0",
        "trace-baseline-1",
        "trace-baseline-2",
        "trace-candidate-0",
        "trace-candidate-1",
        "trace-candidate-2",
    ]
    assert result.verified_resources["workbench_links"]["Candidate Experiment"] == (
        "/?view=runs&run_id=experiment-candidate"
    )
    assert result.verified_resources["workbench_links"]["Trace trace-candidate-2"] == (
        "/?view=traces&trace_id=trace-candidate-2"
    )
    assert "secret" not in repr(result)


def acceptance_result() -> acceptance.LiveToolAcceptanceResult:
    return acceptance.LiveToolAcceptanceResult(
        accepted=True,
        completed_at="2026-09-12T10:11:12+00:00",
        provider={
            "endpoint_origin": "https://provider.example.test",
            "configured_model": "configured-model",
            "response_model": "provider-model-release",
            "challenge_verified": True,
            "usage_present": True,
        },
        dataset={
            "id": "dataset-live",
            "version_id": "dataset-version-frozen",
            "manifest_sha256": "manifest-hash",
        },
        releases={
            "baseline_id": "release-baseline",
            "candidate_id": "release-candidate",
        },
        experiments={
            "baseline_id": "experiment-baseline",
            "baseline_status": "completed",
            "candidate_id": "experiment-candidate",
            "candidate_status": "completed",
            "evidence_complete": True,
        },
        comparison={
            "baseline_run_id": "experiment-baseline",
            "new_failure_count": 0,
            "recovered_case_count": 0,
            "missing_evidence_count": 0,
            "metrics": [
                {"metric_name": "live_business_state", "comparable": True}
            ],
        },
        gate={"run_id": "experiment-candidate", "status": "passed"},
        verified_resources={
            "api_paths": [
                "/projects/project-1/experiments/experiment-baseline",
                "/projects/project-1/traces/trace-baseline-0",
            ],
            "trace_ids": ["trace-baseline-0"],
            "workbench_links": {
                "Baseline Experiment": (
                    "/?view=runs&run_id=experiment-baseline"
                ),
                "Trace trace-baseline-0": (
                    "/?view=traces&trace_id=trace-baseline-0"
                ),
            },
        },
    )


def test_acceptance_artifacts_are_redacted_and_traceable(tmp_path: Path) -> None:
    json_path, markdown_path = acceptance.write_acceptance_artifacts(
        acceptance_result(),
        tmp_path,
    )

    json_text = json_path.read_text(encoding="utf-8")
    markdown_text = markdown_path.read_text(encoding="utf-8")
    combined = json_text + markdown_text

    assert json_path.name == "tool-acceptance-20260912T101112Z.json"
    assert markdown_path.name == "tool-acceptance-20260912T101112Z.md"
    assert "provider-model-release" in combined
    assert "dataset-version-frozen" in combined
    assert "experiment-candidate" in combined
    assert "/projects/project-1/traces/trace-baseline-0" in combined
    assert "/?view=runs&run_id=experiment-baseline" in markdown_text
    assert "/?view=traces&trace_id=trace-baseline-0" in markdown_text
    assert "platform-test-secret" not in combined
    assert "provider-test-secret" not in combined
    assert "Authorization" not in combined
    assert "system_prompt" not in combined
    assert "raw_response" not in combined


def test_cli_does_not_write_artifacts_after_acceptance_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(acceptance, "load_dotenv", lambda **_: False)
    monkeypatch.setattr(
        acceptance.PlatformConfig,
        "from_environment",
        staticmethod(platform_config),
    )
    monkeypatch.setattr(
        LiveProviderConfig,
        "from_environment",
        staticmethod(provider_config),
    )
    monkeypatch.setattr(
        acceptance,
        "run_tool_acceptance",
        lambda *_: (_ for _ in ()).throw(
            acceptance.LiveAcceptanceError("persisted Trace cannot be reopened")
        ),
    )
    writes: list[acceptance.LiveToolAcceptanceResult] = []
    monkeypatch.setattr(
        acceptance,
        "write_acceptance_artifacts",
        lambda result: writes.append(result),
    )

    assert acceptance.main() == 3
    assert writes == []


def test_cli_redacts_platform_and_provider_keys(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    platform_secret = "platform-secret-that-must-not-leak"
    provider_secret = "provider-secret-that-must-not-leak"
    monkeypatch.setenv("AGENT_EVAL_BASE_URL", "http://platform.example.test")
    monkeypatch.setenv("AGENT_EVAL_PROJECT_ID", "project-1")
    monkeypatch.setenv("AGENT_EVAL_API_KEY", platform_secret)
    monkeypatch.setenv("LIVE_ACCEPTANCE_BASE_URL", "https://provider.example.test/v1")
    monkeypatch.setenv("LIVE_ACCEPTANCE_API_KEY", provider_secret)
    monkeypatch.setenv("LIVE_ACCEPTANCE_CHAT_MODEL", "configured-model")
    monkeypatch.setattr(acceptance, "load_dotenv", lambda **_: False)

    def fail(*_: object) -> acceptance.LiveToolAcceptanceResult:
        raise RuntimeError(f"keys were {platform_secret} and {provider_secret}")

    monkeypatch.setattr(acceptance, "run_tool_acceptance", fail)

    assert acceptance.main() == 3
    captured = capsys.readouterr()
    assert platform_secret not in captured.err
    assert provider_secret not in captured.err
    assert captured.err.count("[REDACTED]") == 2
