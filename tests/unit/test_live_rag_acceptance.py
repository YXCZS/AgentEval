from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from tests.live import run_rag_acceptance as acceptance
from tests.live.provider_preflight import (
    LiveEmbeddingConfig,
    LiveProviderConfig,
    LiveProviderPreflightError,
)
from tests.live.run_tool_acceptance import PlatformConfig


def platform_config() -> PlatformConfig:
    return PlatformConfig(
        base_url="http://platform.example.test",
        project_id="project-1",
        api_key="platform-test-secret",
    )


def chat_config() -> LiveProviderConfig:
    return LiveProviderConfig(
        base_url="https://chat.example.test/v1",
        api_key="chat-test-secret",
        model="chat-model",
        timeout_seconds=5,
    )


def embedding_config() -> LiveEmbeddingConfig:
    return LiveEmbeddingConfig(
        base_url="https://embedding.example.test/v1",
        api_key="embedding-test-secret",
        model="embedding-model",
        timeout_seconds=5,
    )


def test_rag_preflights_happen_before_platform_data_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[str] = []

    def fail_preflight(_: LiveProviderConfig) -> object:
        raise LiveProviderPreflightError("chat Provider is unavailable")

    monkeypatch.setattr(acceptance, "run_preflight", fail_preflight)
    monkeypatch.setattr(httpx, "Client", lambda **_: constructed.append("platform-http"))

    with pytest.raises(LiveProviderPreflightError):
        acceptance.run_rag_acceptance(platform_config(), chat_config(), embedding_config())

    assert constructed == []


def test_persisted_trace_requires_retriever_document_ids_and_both_usage_kinds() -> None:
    trace = {
        "trace_id": "trace-rag-1",
        "spans": [
            {
                "kind": "retrieval",
                "usage": {"input_tokens": 8, "total_tokens": 8},
                "attributes": {"retrieval.document_ids": ["policy-refund-window"]},
            },
            {
                "kind": "llm",
                "usage": {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
            },
        ],
    }

    verified = acceptance._verify_persisted_rag_trace(trace)

    assert verified["retrieved_document_ids"] == ["policy-refund-window"]
    trace["spans"][0]["attributes"]["retrieval.document_ids"] = []
    with pytest.raises(acceptance.LiveRagAcceptanceError, match="document IDs"):
        acceptance._verify_persisted_rag_trace(trace)


def test_rag_cli_reports_missing_configuration_without_import_failure() -> None:
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
        "LIVE_ACCEPTANCE_EMBEDDING_BASE_URL",
        "LIVE_ACCEPTANCE_EMBEDDING_API_KEY",
        "LIVE_ACCEPTANCE_EMBEDDING_MODEL",
    ):
        environment.pop(name, None)

    result = subprocess.run(
        [sys.executable, "tests/live/run_rag_acceptance.py"],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "live RAG acceptance configuration error" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr
