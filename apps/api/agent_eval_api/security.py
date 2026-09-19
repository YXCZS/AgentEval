"""Password hashing and JWT helpers for user authentication."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import jwt

from agent_eval_api.settings import Settings

_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16
_HASH_BYTES = 32


def hash_password(password: str) -> str:
    """Hash a plaintext password using PBKDF2-HMAC-SHA256 with a random salt.

    The stored format is ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`` so
    iteration count and salt are self-describing and can be upgraded later.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS, _HASH_BYTES)
    return (
        f"pbkdf2_sha256${_PBKDF2_ITERATIONS}$"
        f"{base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"
    )


def verify_password(password: str, encoded: str) -> bool:
    """Verify a plaintext password against a stored PBKDF2 hash string."""
    try:
        scheme, iterations_str, salt_b64, hash_b64 = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_str)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations, len(expected))
    return hmac.compare_digest(digest, expected)


def create_access_token(user_id: str, settings: Settings) -> str:
    """Issue a signed JWT for a user, expiring after the configured lifetime."""
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_token_expire_minutes),
    }
    return jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_access_token(token: str, settings: Settings) -> str | None:
    """Return the user id for a valid, unexpired access token, or None."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
        )
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access" or not isinstance(payload.get("sub"), str):
        return None
    return payload["sub"]
