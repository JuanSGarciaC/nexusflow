"""Cifrado, adaptadores de autenticación, validación de hosts y redacción."""

from __future__ import annotations

import base64
import uuid
from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from nexusflow.engine.executors import REDACTED, _redact
from nexusflow.integrations.adapters import ADAPTERS, CredentialType, parse_secret
from nexusflow.integrations.crypto import (
    CredentialCipher,
    CredentialDecryptionError,
    key_fingerprint,
)
from nexusflow.integrations.schemas import CredentialCreate

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()
SECRET = {"token": "tok_live_1234567890abcdef"}


def test_encrypt_decrypt_roundtrip_without_plaintext() -> None:
    cipher = CredentialCipher(KEY_A)
    cid = uuid.uuid4()
    token = cipher.encrypt(cid, "crm", SECRET)
    assert SECRET["token"] not in token
    assert cipher.decrypt(cid, "crm", token) == SECRET


@pytest.mark.parametrize(("other_id", "other_name"), [(True, False), (False, True)])
def test_ciphertext_is_bound_to_its_row(other_id: bool, other_name: bool) -> None:
    cipher = CredentialCipher(KEY_A)
    cid = uuid.uuid4()
    token = cipher.encrypt(cid, "crm", SECRET)
    with pytest.raises(CredentialDecryptionError, match="no pertenece"):
        cipher.decrypt(uuid.uuid4() if other_id else cid, "erp" if other_name else "crm", token)


def test_tampered_ciphertext_rejected() -> None:
    cipher = CredentialCipher(KEY_A)
    cid = uuid.uuid4()
    raw = bytearray(base64.urlsafe_b64decode(cipher.encrypt(cid, "crm", SECRET)))
    raw[-40] ^= 0x01  # un bit del cuerpo cifrado
    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt(cid, "crm", base64.urlsafe_b64encode(bytes(raw)).decode())


def test_key_rotation() -> None:
    cid = uuid.uuid4()
    old_token = CredentialCipher(KEY_A).encrypt(cid, "crm", SECRET)

    rotating = CredentialCipher(f"{KEY_B},{KEY_A}")  # B nueva principal, A aún descifra
    assert rotating.decrypt(cid, "crm", old_token) == SECRET
    new_token = rotating.rotate(old_token)
    assert rotating.primary_fingerprint == key_fingerprint(KEY_B.encode())

    # Retirada la clave antigua, lo recifrado sigue legible; lo antiguo ya no.
    only_new = CredentialCipher(KEY_B)
    assert only_new.decrypt(cid, "crm", new_token) == SECRET
    with pytest.raises(CredentialDecryptionError):
        only_new.decrypt(cid, "crm", old_token)


def test_fingerprint_does_not_reveal_key() -> None:
    fingerprint = key_fingerprint(KEY_A.encode())
    assert len(fingerprint) == 12
    assert fingerprint not in KEY_A


# ------------------------------------------------------------------ adaptadores


def test_bearer_adapter() -> None:
    secret = parse_secret(CredentialType.BEARER_TOKEN, SECRET)
    adapter = ADAPTERS[CredentialType.BEARER_TOKEN]
    assert adapter.headers(secret) == {"Authorization": f"Bearer {SECRET['token']}"}
    assert adapter.hint(secret) == "…cdef"


def test_api_key_adapter_custom_header() -> None:
    secret = parse_secret(
        CredentialType.API_KEY_HEADER, {"header_name": "X-Partner-Key", "value": "k" * 20}
    )
    assert ADAPTERS[CredentialType.API_KEY_HEADER].headers(secret) == {"X-Partner-Key": "k" * 20}


def test_basic_adapter_encodes_and_redacts_both_forms() -> None:
    secret = parse_secret(CredentialType.BASIC_AUTH, {"username": "ana", "password": "p4ss"})
    adapter = ADAPTERS[CredentialType.BASIC_AUTH]
    encoded = base64.b64encode(b"ana:p4ss").decode()
    assert adapter.headers(secret) == {"Authorization": f"Basic {encoded}"}
    assert set(adapter.secret_values(secret)) == {"p4ss", encoded}
    assert "p4ss" not in adapter.hint(secret)


@pytest.mark.parametrize(
    ("credential_type", "data"),
    [
        (CredentialType.BEARER_TOKEN, {}),
        (CredentialType.BEARER_TOKEN, {"token": "x", "extra": 1}),
        (CredentialType.BASIC_AUTH, {"username": "a:b", "password": "x"}),
        (CredentialType.API_KEY_HEADER, {"header_name": "Host", "value": "x"}),
        (CredentialType.API_KEY_HEADER, {"header_name": "X Bad", "value": "x"}),
    ],
)
def test_invalid_secrets_rejected(credential_type: CredentialType, data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        parse_secret(credential_type, data)


# ------------------------------------------------------------------ hosts permitidos


def _create(hosts: list[str]) -> CredentialCreate:
    return CredentialCreate.model_validate(
        {"name": "crm", "type": "bearer_token", "allowed_hosts": hosts, "secret": SECRET}
    )


def test_hosts_are_normalized() -> None:
    assert _create(["API.Example.com", "10.0.0.1"]).allowed_hosts == ["api.example.com", "10.0.0.1"]


@pytest.mark.parametrize(
    "host", ["https://api.example.com", "api.example.com/path", "*.example.com", ""]
)
def test_hosts_must_be_bare_hostnames(host: str) -> None:
    with pytest.raises(ValidationError):
        _create([host])


# ------------------------------------------------------------------ redacción


def test_redact_nested_values() -> None:
    output = {"headers": {"authorization": "Bearer abc123"}, "items": ["abc123", 5], "ok": True}
    assert _redact(output, ["abc123"]) == {
        "headers": {"authorization": f"Bearer {REDACTED}"},
        "items": [REDACTED, 5],
        "ok": True,
    }
    assert _redact(output, []) is output
