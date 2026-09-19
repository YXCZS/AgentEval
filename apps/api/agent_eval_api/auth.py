"""Small, project-scoped authentication layer for the first self-hosted release."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_eval_api.bootstrap import DEFAULT_PROJECT_ID, LEGACY_PROJECT_ID
from agent_eval_api.db import ApiKeyRecord, ProjectRecord, get_session_factory, utc_now
from agent_eval_api.settings import Settings, get_settings


@dataclass(frozen=True)
class AuthContext:
    project_id: str
    principal_type: Literal["browser", "agent", "ci"]
    credential_id: str | None = None


def hash_project_key(raw_key: str, salt: str) -> str:
    """Hash a project key with a deployment-specific salt."""

    return hmac.new(salt.encode(), raw_key.encode(), hashlib.sha256).hexdigest()


def issue_project_key(
    project_id: str, settings: Settings, *, name: str = "generated"
) -> tuple[str, ApiKeyRecord]:
    """Create a key record and return the plaintext only to the caller once."""

    raw_secret = secrets.token_urlsafe(32)
    raw_key = f"aek_{project_id}_{raw_secret}"
    record = ApiKeyRecord(
        project_id=project_id,
        name=name,
        key_hash=hash_project_key(raw_key, settings.api_key_salt.get_secret_value()),
        key_prefix=raw_key[:16],
    )
    return raw_key, record


def issue_dev_session(project_id: str, settings: Settings) -> str:
    """Issue the project-bound browser token used by the single-workspace dev login."""

    secret = settings.workspace_session_secret.get_secret_value()
    return f"dev:{project_id}:{secret}"


def get_db() -> Iterator[Session]:
    """Yield one request-scoped database session and always return it to the pool."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def require_project_access(
    project_id: str,
    x_project_key: str | None = Header(default=None),
    x_workspace_session: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> AuthContext:
    # Resolve the project before checking a credential. This prevents a
    # correctly shaped development session for a non-existent project from
    # becoming an authenticated context.
    if db.get(ProjectRecord, project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid project"
        )

    if x_project_key:
        key_hash = hash_project_key(x_project_key, settings.api_key_salt.get_secret_value())
        record = db.scalar(
            select(ApiKeyRecord).where(
                ApiKeyRecord.project_id == project_id,
                ApiKeyRecord.key_hash == key_hash,
                ApiKeyRecord.active.is_(True),
            )
        )
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid project key"
            )
        record.last_used_at = utc_now()
        db.commit()
        return AuthContext(project_id=project_id, principal_type="agent", credential_id=record.id)

    if authorization and authorization.lower().startswith("bearer "):
        from agent_eval_api.security import decode_access_token
        from agent_eval_api.db import UserRecord

        token = authorization.split(" ", 1)[1].strip()
        user_id = decode_access_token(token, settings)
        if user_id is not None:
            user = db.get(UserRecord, user_id)
            if user is not None and user.active and user.project_id == project_id:
                return AuthContext(project_id=project_id, principal_type="browser", credential_id=user.id)

    if x_workspace_session:
        valid_sessions = [issue_dev_session(project_id, settings)]
        if project_id == DEFAULT_PROJECT_ID:
            valid_sessions.append(issue_dev_session(LEGACY_PROJECT_ID, settings))
        if any(hmac.compare_digest(x_workspace_session, candidate) for candidate in valid_sessions):
            return AuthContext(project_id=project_id, principal_type="browser")

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
