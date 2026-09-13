from __future__ import annotations

from types import SimpleNamespace

import pytest
from tests.live import provider_preflight
from tests.live.provider_preflight import (
    LiveEmbeddingConfig,
    LiveEmbeddingEvidence,
    LiveProviderConfig,
    LiveProviderConfigurationError,
    LiveProviderPreflightError,
    run_preflight,
)


class FakeCompletions:
    def __init__(self, *, usage: object | None = None, return_challenge: bool = True) -> None:
        self.usage = usage
        self.return_challenge = return_challenge

    def create(self, **kwargs: object) -> object:
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        prompt = messages[0]["content"]
        challenge = str(prompt).rsplit(" ", 1)[-1]
        content = challenge if self.return_challenge else "static fixture response"
        usage = self.usage or SimpleNamespace(
            prompt_tokens=12,
            completion_tokens=8,
            total_tokens=20,
        )
        return SimpleNamespace(
            id="chatcmpl-live-request",
            model="provider-model-release",
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=usage,
        )


class FakeOpenAI:
    def __init__(self, completions: FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeEmbeddings:
    def create(self, **_: object) -> object:
        return SimpleNamespace(
            model="embedding-model-release",
            data=[SimpleNamespace(index=0, embedding=[0.1, 0.2, 0.3])],
            usage=SimpleNamespace(prompt_tokens=4),
        )


class FakeEmbeddingOpenAI:
    def __init__(self) -> None:
        self.embeddings = FakeEmbeddings()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def config(**overrides: object) -> LiveProviderConfig:
    values: dict[str, object] = {
        "base_url": "https://provider.example.test/v1",
        "api_key": "test-only-secret",
        "model": "configured-model",
        "timeout_seconds": 5.0,
    }
    values.update(overrides)
    return LiveProviderConfig(**values)  # type: ignore[arg-type]


def install_client(monkeypatch: pytest.MonkeyPatch, completions: FakeCompletions) -> FakeOpenAI:
    client = FakeOpenAI(completions)
    monkeypatch.setattr(provider_preflight, "OpenAI", lambda **_: client)
    return client


@pytest.mark.parametrize(
    "missing_name",
    [
        "LIVE_ACCEPTANCE_BASE_URL",
        "LIVE_ACCEPTANCE_API_KEY",
        "LIVE_ACCEPTANCE_CHAT_MODEL",
    ],
)
def test_environment_requires_every_live_dependency(
    monkeypatch: pytest.MonkeyPatch, missing_name: str
) -> None:
    values = {
        "LIVE_ACCEPTANCE_BASE_URL": "https://provider.example.test/v1",
        "LIVE_ACCEPTANCE_API_KEY": "test-only-secret",
        "LIVE_ACCEPTANCE_CHAT_MODEL": "configured-model",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing_name)

    with pytest.raises(LiveProviderConfigurationError, match=missing_name):
        LiveProviderConfig.from_environment()


def test_rag_embedding_provider_is_an_explicit_live_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "LIVE_ACCEPTANCE_EMBEDDING_BASE_URL",
        "LIVE_ACCEPTANCE_EMBEDDING_API_KEY",
        "LIVE_ACCEPTANCE_EMBEDDING_MODEL",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_API_KEY",
        "EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(LiveProviderConfigurationError, match="LIVE_ACCEPTANCE_EMBEDDING_BASE_URL"):
        LiveEmbeddingConfig.from_environment()

    monkeypatch.setenv("LIVE_ACCEPTANCE_EMBEDDING_BASE_URL", "https://embedding.example.test/v1")
    monkeypatch.setenv("LIVE_ACCEPTANCE_EMBEDDING_API_KEY", "embedding-test-secret")
    monkeypatch.setenv("LIVE_ACCEPTANCE_EMBEDDING_MODEL", "text-embedding-3-small")
    assert LiveEmbeddingConfig.from_environment().model == "text-embedding-3-small"


def test_rag_embedding_provider_supports_concise_environment_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "LIVE_ACCEPTANCE_EMBEDDING_BASE_URL",
        "LIVE_ACCEPTANCE_EMBEDDING_API_KEY",
        "LIVE_ACCEPTANCE_EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embedding.example.test/v1")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-test-secret")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-v4")

    config = LiveEmbeddingConfig.from_environment()

    assert config.base_url == "https://embedding.example.test/v1"
    assert config.model == "text-embedding-v4"


def test_rag_embedding_provider_prefers_acceptance_environment_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://concise.example.test/v1")
    monkeypatch.setenv("EMBEDDING_API_KEY", "concise-test-secret")
    monkeypatch.setenv("EMBEDDING_MODEL", "concise-model")
    monkeypatch.setenv("LIVE_ACCEPTANCE_EMBEDDING_BASE_URL", "https://live.example.test/v1")
    monkeypatch.setenv("LIVE_ACCEPTANCE_EMBEDDING_API_KEY", "live-test-secret")
    monkeypatch.setenv("LIVE_ACCEPTANCE_EMBEDDING_MODEL", "live-model")

    config = LiveEmbeddingConfig.from_environment()

    assert config.base_url == "https://live.example.test/v1"
    assert config.model == "live-model"


@pytest.mark.parametrize(
    "base_url",
    ["provider.example.test/v1", "ftp://provider.example.test", "https://user:pass@x/v1"],
)
def test_environment_rejects_unsafe_provider_urls(
    monkeypatch: pytest.MonkeyPatch, base_url: str
) -> None:
    monkeypatch.setenv("LIVE_ACCEPTANCE_BASE_URL", base_url)
    monkeypatch.setenv("LIVE_ACCEPTANCE_API_KEY", "test-only-secret")
    monkeypatch.setenv("LIVE_ACCEPTANCE_CHAT_MODEL", "configured-model")

    with pytest.raises(LiveProviderConfigurationError):
        LiveProviderConfig.from_environment()


def test_preflight_returns_redacted_real_call_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = install_client(monkeypatch, FakeCompletions())

    evidence = run_preflight(config())

    assert evidence.endpoint_origin == "https://provider.example.test"
    assert evidence.configured_model == "configured-model"
    assert evidence.response_model == "provider-model-release"
    assert evidence.upstream_request_id == "chatcmpl-live-request"
    assert evidence.total_tokens == 20
    assert evidence.challenge_verified is True
    assert client.closed is True
    assert "secret" not in repr(evidence)


def test_embedding_preflight_requires_real_vector_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.live import provider_preflight

    client = FakeEmbeddingOpenAI()
    monkeypatch.setattr(provider_preflight, "OpenAI", lambda **_: client)
    evidence: LiveEmbeddingEvidence = provider_preflight.run_embedding_preflight(
        LiveEmbeddingConfig(
            base_url="https://embedding.example.test/v1",
            api_key="embedding-test-secret",
            model="configured-embedding-model",
            timeout_seconds=5,
        )
    )

    assert evidence.vector_dimensions == 3
    assert evidence.input_tokens == 4
    assert evidence.endpoint_origin == "https://embedding.example.test"
    assert client.closed is True


def test_preflight_rejects_static_response(monkeypatch: pytest.MonkeyPatch) -> None:
    install_client(monkeypatch, FakeCompletions(return_challenge=False))

    with pytest.raises(LiveProviderPreflightError, match="randomized challenge"):
        run_preflight(config())


def test_cli_returns_nonzero_and_redacts_key_on_provider_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "live-secret-that-must-not-leak"

    class FailingCompletions:
        def create(self, **_: object) -> object:
            raise RuntimeError(f"Authorization: Bearer {secret}")

    install_client(monkeypatch, FailingCompletions())  # type: ignore[arg-type]
    monkeypatch.setenv("LIVE_ACCEPTANCE_BASE_URL", "https://provider.example.test/v1")
    monkeypatch.setenv("LIVE_ACCEPTANCE_API_KEY", secret)
    monkeypatch.setenv("LIVE_ACCEPTANCE_CHAT_MODEL", "configured-model")

    assert provider_preflight.main() == 3
    captured = capsys.readouterr()
    assert secret not in captured.err
    assert "[REDACTED]" in captured.err


def test_preflight_requires_upstream_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = FakeCompletions()
    completions.usage = SimpleNamespace(
        prompt_tokens=12,
        completion_tokens=8,
        total_tokens=None,
    )
    install_client(monkeypatch, completions)

    with pytest.raises(LiveProviderPreflightError, match="usage.total_tokens"):
        run_preflight(config())


def test_preflight_redacts_key_from_provider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "test-only-secret"

    class FailingCompletions:
        def create(self, **_: object) -> object:
            raise RuntimeError(f"Authorization: Bearer {secret}")

    install_client(monkeypatch, FailingCompletions())  # type: ignore[arg-type]

    with pytest.raises(LiveProviderPreflightError) as raised:
        run_preflight(config(api_key=secret))

    assert secret not in str(raised.value)
    assert "[REDACTED]" in str(raised.value)
