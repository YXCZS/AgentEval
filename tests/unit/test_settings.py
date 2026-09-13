from agent_eval_api.settings import Settings


def test_settings_json_representation_masks_secrets() -> None:
    settings = Settings(
        api_key_salt="top-secret",
        credential_encryption_key="base64-encryption-key",
        external_evaluator_secrets={"team-judge": "judge-signing-secret"},
    )

    serialized = settings.model_dump_json()

    assert "top-secret" not in serialized
    assert "base64-encryption-key" not in serialized
    assert "judge-signing-secret" not in serialized
    assert "**********" in serialized


def test_credential_encryption_settings_use_prefixed_environment_names(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_EVAL_CREDENTIAL_ENCRYPTION_KEY", "local-untracked-key")
    monkeypatch.setenv("AGENT_EVAL_CREDENTIAL_ENCRYPTION_KEY_ID", "rotation-2026")

    settings = Settings()

    assert settings.credential_encryption_key is not None
    assert settings.credential_encryption_key.get_secret_value() == "local-untracked-key"
    assert settings.credential_encryption_key_id == "rotation-2026"


def test_external_evaluator_secret_map_uses_prefixed_json_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "AGENT_EVAL_EXTERNAL_EVALUATOR_SECRETS",
        '{"team-judge":"runtime-signing-secret"}',
    )

    settings = Settings()

    assert settings.external_evaluator_secrets[
        "team-judge"
    ].get_secret_value() == "runtime-signing-secret"
    assert "runtime-signing-secret" not in settings.model_dump_json()
