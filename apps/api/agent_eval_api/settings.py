from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder secrets shipped with the source tree. A production deployment must
# override every one of these or the process refuses to start.
_INSECURE_PLACEHOLDER_SECRETS = frozenset(
    {
        "development-only-change-me",
        "development-session-secret",
        "development-jwt-secret-change-me",
    }
)


class Settings(BaseSettings):
    """Runtime settings. Secrets are never serialized into API payloads."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./agent_eval.db"
    redis_url: str = "redis://localhost:6379/0"
    api_key_salt: SecretStr = SecretStr("development-only-change-me")
    workspace_session_secret: SecretStr = SecretStr("development-session-secret")
    jwt_secret: SecretStr = Field(
        default_factory=lambda: SecretStr("development-jwt-secret-change-me"),
        validation_alias="JWT_SECRET",
    )
    jwt_access_token_expire_minutes: int = Field(
        default=60 * 24 * 7, ge=5, le=60 * 24 * 365, validation_alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES"
    )
    bootstrap_admin_email: str | None = Field(
        default=None, validation_alias="AGENT_EVAL_BOOTSTRAP_ADMIN_EMAIL"
    )
    bootstrap_admin_password: SecretStr | None = Field(
        default=None, validation_alias="AGENT_EVAL_BOOTSTRAP_ADMIN_PASSWORD"
    )
    credential_encryption_key: SecretStr | None = Field(
        default=None,
        validation_alias="AGENT_EVAL_CREDENTIAL_ENCRYPTION_KEY",
    )
    credential_encryption_key_id: str = Field(
        default="primary",
        validation_alias="AGENT_EVAL_CREDENTIAL_ENCRYPTION_KEY_ID",
        min_length=1,
    )
    cors_origins: str = Field(
        default="",
        validation_alias="AGENT_EVAL_CORS_ORIGINS",
        description=(
            "Comma-separated extra CORS origins (e.g. production frontend URLs) "
            "appended to the built-in localhost development allowlist."
        ),
    )
    external_evaluator_secrets: dict[str, SecretStr] = Field(
        default_factory=dict,
        validation_alias="AGENT_EVAL_EXTERNAL_EVALUATOR_SECRETS",
    )
    worker_max_concurrency: int = Field(default=8, ge=1, le=256)
    worker_admission_retry_seconds: float = Field(default=0.5, gt=0.0, le=30.0)
    trace_max_request_bytes: int = Field(default=1_048_576, ge=64, le=50_000_000)
    trace_max_spans: int = Field(default=1_000, ge=1, le=100_000)
    trace_max_nesting_depth: int = Field(default=32, ge=0, le=1_000)
    trace_max_field_bytes: int = Field(default=16_384, ge=64, le=10_485_760)
    trace_redaction_field_names: list[str] = Field(
        default_factory=lambda: [
            "api_key",
            "authorization",
            "token",
            "secret",
            "password",
            "credential",
            "cookie",
            "access_key",
        ]
    )

    @field_validator("database_url", "redis_url")
    @classmethod
    def must_have_scheme(cls, value: str) -> str:
        if "://" not in value:
            raise ValueError("must be a URL")
        return value

    @model_validator(mode="after")
    def _reject_placeholder_secrets_in_production(self) -> "Settings":
        """Fail closed in production when a crypto secret is still a default.

        The dev-only placeholders are meant to make local development "just
        work". Running them in production would let anyone forge JWTs or project
        keys, so we refuse to start instead of silently degrading security.
        """
        if self.app_env != "production":
            return self

        checks: list[tuple[str, str]] = [
            ("JWT_SECRET", self.jwt_secret.get_secret_value()),
            ("API_KEY_SALT", self.api_key_salt.get_secret_value()),
            ("WORKSPACE_SESSION_SECRET", self.workspace_session_secret.get_secret_value()),
        ]
        insecure = [name for name, value in checks if value in _INSECURE_PLACEHOLDER_SECRETS]
        if insecure:
            names = ", ".join(insecure)
            raise ValueError(
                f"refusing to start in production: {names} must be set to strong, "
                "non-default secrets (the development placeholders are not secure)"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
