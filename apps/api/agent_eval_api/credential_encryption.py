"""Authenticated encryption for platform-managed provider credentials."""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agent_eval_api.settings import Settings

_AES_256_KEY_BYTES = 32
_GCM_NONCE_BYTES = 12


class CredentialEncryptionError(RuntimeError):
    """A secret-safe credential encryption or configuration failure."""


@dataclass(frozen=True)
class EncryptedCredential:
    ciphertext: bytes
    nonce: bytes
    key_id: str
    mask: str


def mask_credential(credential: str) -> str:
    """Return a recognition hint without exposing a short credential."""

    if len(credential) <= 8:
        return "********"
    return f"{credential[:3]}...{credential[-4:]}"


def _decode_key(encoded_key: str) -> bytes:
    try:
        key = base64.b64decode(encoded_key, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CredentialEncryptionError(
            "credential encryption key must be valid base64"
        ) from exc
    if len(key) != _AES_256_KEY_BYTES:
        raise CredentialEncryptionError(
            "credential encryption key must decode to exactly 32 bytes"
        )
    return key


def _associated_data(
    purpose: str, project_id: str, connection_id: str, key_id: str
) -> bytes:
    return json.dumps(
        [purpose, project_id, connection_id, key_id],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")


class CredentialCipher:
    """AES-256-GCM cipher bound to one deployment key identifier."""

    def __init__(self, *, encoded_key: str, key_id: str) -> None:
        if not key_id.strip():
            raise CredentialEncryptionError("credential encryption key id is required")
        self._cipher = AESGCM(_decode_key(encoded_key))
        self.key_id = key_id

    @classmethod
    def from_settings(cls, settings: Settings) -> CredentialCipher:
        if settings.credential_encryption_key is None:
            raise CredentialEncryptionError("credential encryption key is not configured")
        return cls(
            encoded_key=settings.credential_encryption_key.get_secret_value(),
            key_id=settings.credential_encryption_key_id,
        )

    def encrypt(
        self,
        credential: str,
        *,
        project_id: str,
        connection_id: str,
        purpose: str = "provider_connection",
    ) -> EncryptedCredential:
        if not credential:
            raise CredentialEncryptionError("provider credential must not be empty")
        nonce = os.urandom(_GCM_NONCE_BYTES)
        ciphertext = self._cipher.encrypt(
            nonce,
            credential.encode("utf-8"),
            _associated_data(purpose, project_id, connection_id, self.key_id),
        )
        return EncryptedCredential(
            ciphertext=ciphertext,
            nonce=nonce,
            key_id=self.key_id,
            mask=mask_credential(credential),
        )

    def decrypt(
        self,
        *,
        ciphertext: bytes,
        nonce: bytes,
        stored_key_id: str,
        project_id: str,
        connection_id: str,
        purpose: str = "provider_connection",
    ) -> str:
        if stored_key_id != self.key_id:
            raise CredentialEncryptionError(
                "credential encryption key id is not available"
            )
        if len(nonce) != _GCM_NONCE_BYTES:
            raise CredentialEncryptionError("encrypted credential nonce is invalid")
        try:
            plaintext = self._cipher.decrypt(
                nonce,
                ciphertext,
                _associated_data(purpose, project_id, connection_id, stored_key_id),
            )
            return plaintext.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError) as exc:
            raise CredentialEncryptionError(
                "encrypted credential failed authentication"
            ) from exc

    def rotate(
        self,
        *,
        destination: CredentialCipher,
        ciphertext: bytes,
        nonce: bytes,
        stored_key_id: str,
        project_id: str,
        connection_id: str,
        purpose: str = "provider_connection",
    ) -> EncryptedCredential:
        credential = self.decrypt(
            ciphertext=ciphertext,
            nonce=nonce,
            stored_key_id=stored_key_id,
            project_id=project_id,
            connection_id=connection_id,
            purpose=purpose,
        )
        return destination.encrypt(
            credential,
            project_id=project_id,
            connection_id=connection_id,
            purpose=purpose,
        )


def validate_runtime_credential_configuration(settings: Settings) -> None:
    """Fail closed when a production process cannot protect credentials."""

    if settings.app_env == "production":
        CredentialCipher.from_settings(settings)
