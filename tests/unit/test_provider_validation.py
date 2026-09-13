from types import SimpleNamespace

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    NotFoundError,
)

import agent_eval_api.provider_validation as provider_validation
from agent_eval_api.provider_validation import (
    ProviderValidationError,
    validate_provider_connection,
)


class FakeCompletions:
    def __init__(self, response: object | Exception) -> None:
        self.response = response
        self.request: dict[str, object] | None = None

    def create(self, **kwargs: object) -> object:
        self.request = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        challenge = str(messages[0]["content"]).rsplit(" ", 1)[-1]
        response = self.response
        choices = response.choices  # type: ignore[attr-defined]
        if choices and choices[0].message.content == "__challenge__":
            choices[0].message.content = challenge
        return response


class FakeOpenAI:
    def __init__(self, completions: FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def response(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": "chatcmpl-provider-test",
        "model": "deepseek-chat",
        "choices": [SimpleNamespace(message=SimpleNamespace(content="__challenge__"))],
        "usage": SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=4,
            total_tokens=14,
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def install(monkeypatch: pytest.MonkeyPatch, result: object | Exception) -> FakeOpenAI:
    client = FakeOpenAI(FakeCompletions(result))
    monkeypatch.setattr(provider_validation, "OpenAI", lambda **_: client)
    return client


def test_real_validation_requires_challenge_identity_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = install(monkeypatch, response())

    evidence = validate_provider_connection(
        base_url="https://api.deepseek.com/v1",
        api_key="test-only-secret",
        model="deepseek-chat",
        timeout_seconds=5,
    )

    assert evidence.response_model == "deepseek-chat"
    assert evidence.upstream_request_id == "chatcmpl-provider-test"
    assert evidence.total_tokens == 14
    assert client.closed is True
    assert client.chat.completions.request["temperature"] == 0
    assert client.chat.completions.request["max_tokens"] == 64


@pytest.mark.parametrize(
    ("invalid_response", "message"),
    [
        (response(choices=[]), "one choice"),
        (
            response(choices=[SimpleNamespace(message=SimpleNamespace(content="wrong"))]),
            "randomized challenge",
        ),
        (response(id=""), "request id or model"),
        (response(usage=None), "usage metadata"),
        (
            response(
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=4,
                    total_tokens=1,
                )
            ),
            "usage totals",
        ),
    ],
)
def test_malformed_provider_response_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    invalid_response: object,
    message: str,
) -> None:
    install(monkeypatch, invalid_response)

    with pytest.raises(ProviderValidationError, match=message):
        validate_provider_connection(
            base_url="https://api.deepseek.com/v1",
            api_key="test-only-secret",
            model="deepseek-chat",
            timeout_seconds=5,
        )


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            AuthenticationError(
                "contains test-only-secret",
                response=httpx.Response(401, request=httpx.Request("POST", "https://x")),
                body=None,
            ),
            "rejected the credential",
        ),
        (
            NotFoundError(
                "contains test-only-secret",
                response=httpx.Response(404, request=httpx.Request("POST", "https://x")),
                body=None,
            ),
            "endpoint or model was not found",
        ),
        (
            APITimeoutError(request=httpx.Request("POST", "https://x")),
            "request timed out",
        ),
        (
            APIConnectionError(request=httpx.Request("POST", "https://x")),
            "endpoint is unreachable",
        ),
    ],
)
def test_provider_errors_are_classified_without_upstream_body_or_key(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    message: str,
) -> None:
    install(monkeypatch, error)

    with pytest.raises(ProviderValidationError, match=message) as raised:
        validate_provider_connection(
            base_url="https://api.deepseek.com/v1",
            api_key="test-only-secret",
            model="deepseek-chat",
            timeout_seconds=5,
        )

    assert "test-only-secret" not in str(raised.value)
