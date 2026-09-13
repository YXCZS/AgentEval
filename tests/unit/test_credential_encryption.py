import base64

import pytest

import agent_eval_api.main as api_main
from agent_eval_api.credential_encryption import (
    CredentialCipher,
    CredentialEncryptionError,
    mask_credential,
    validate_runtime_credential_configuration,
)
from agent_eval_api.settings import Settings


def _key(byte: int) -> str:
    return base64.b64encode(bytes([byte]) * 32).decode("ascii")


def test_aes_gcm_round_trip_uses_random_nonce_and_record_bound_aad() -> None:
    cipher = CredentialCipher(encoded_key=_key(1), key_id="key-a")

    first = cipher.encrypt("sk-real-provider-secret", project_id="p1", connection_id="c1")
    second = cipher.encrypt("sk-real-provider-secret", project_id="p1", connection_id="c1")

    assert len(first.nonce) == 12
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext
    assert first.key_id == "key-a"
    assert first.mask == "sk-...cret"
    assert cipher.decrypt(
        ciphertext=first.ciphertext,
        nonce=first.nonce,
        stored_key_id=first.key_id,
        project_id="p1",
        connection_id="c1",
    ) == "sk-real-provider-secret"

    for project_id, connection_id in (("p2", "c1"), ("p1", "c2")):
        with pytest.raises(
            CredentialEncryptionError, match="failed authentication"
        ):
            cipher.decrypt(
                ciphertext=first.ciphertext,
                nonce=first.nonce,
                stored_key_id=first.key_id,
                project_id=project_id,
                connection_id=connection_id,
            )


def test_tampering_wrong_key_and_wrong_key_id_fail_closed() -> None:
    cipher = CredentialCipher(encoded_key=_key(2), key_id="key-a")
    encrypted = cipher.encrypt("provider-secret", project_id="p1", connection_id="c1")

    tampered = bytearray(encrypted.ciphertext)
    tampered[-1] ^= 1
    with pytest.raises(CredentialEncryptionError, match="failed authentication"):
        cipher.decrypt(
            ciphertext=bytes(tampered),
            nonce=encrypted.nonce,
            stored_key_id=encrypted.key_id,
            project_id="p1",
            connection_id="c1",
        )

    wrong_key = CredentialCipher(encoded_key=_key(3), key_id="key-a")
    with pytest.raises(CredentialEncryptionError, match="failed authentication"):
        wrong_key.decrypt(
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
            stored_key_id=encrypted.key_id,
            project_id="p1",
            connection_id="c1",
        )

    with pytest.raises(CredentialEncryptionError, match="key id is not available"):
        cipher.decrypt(
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
            stored_key_id="key-b",
            project_id="p1",
            connection_id="c1",
        )


def test_rotation_reencrypts_with_new_key_id() -> None:
    source = CredentialCipher(encoded_key=_key(4), key_id="key-old")
    destination = CredentialCipher(encoded_key=_key(5), key_id="key-new")
    encrypted = source.encrypt("provider-secret", project_id="p1", connection_id="c1")

    rotated = source.rotate(
        destination=destination,
        ciphertext=encrypted.ciphertext,
        nonce=encrypted.nonce,
        stored_key_id=encrypted.key_id,
        project_id="p1",
        connection_id="c1",
    )

    assert rotated.key_id == "key-new"
    assert rotated.nonce != encrypted.nonce
    assert rotated.ciphertext != encrypted.ciphertext
    assert destination.decrypt(
        ciphertext=rotated.ciphertext,
        nonce=rotated.nonce,
        stored_key_id=rotated.key_id,
        project_id="p1",
        connection_id="c1",
    ) == "provider-secret"


def test_key_validation_and_production_configuration_fail_closed() -> None:
    for invalid_key in ("not-base64", base64.b64encode(b"short").decode("ascii")):
        with pytest.raises(CredentialEncryptionError):
            CredentialCipher(encoded_key=invalid_key, key_id="primary")

    with pytest.raises(CredentialEncryptionError, match="not configured"):
        validate_runtime_credential_configuration(
            Settings(app_env="production", credential_encryption_key=None)
        )

    validate_runtime_credential_configuration(
        Settings(app_env="development", credential_encryption_key=None)
    )
    validate_runtime_credential_configuration(
        Settings(
            app_env="production",
            credential_encryption_key=_key(6),
            credential_encryption_key_id="production-key",
        )
    )


def test_short_credentials_are_fully_masked() -> None:
    assert mask_credential("short") == "********"


def test_api_startup_rejects_missing_or_invalid_production_key(monkeypatch) -> None:
    for key in (None, "not-base64"):
        settings = Settings(app_env="production", credential_encryption_key=key)
        monkeypatch.setattr(api_main, "get_settings", lambda settings=settings: settings)

        with pytest.raises(CredentialEncryptionError):
            api_main.create_app()
