"""Credenciales de punta a punta: API, almacenamiento cifrado y uso en el motor."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from nexusflow.auth.permissions import Role
from nexusflow.engine.models import RunStatus
from nexusflow.integrations.adapters import CredentialType
from nexusflow.integrations.crypto import CredentialCipher
from nexusflow.integrations.models import Credential
from nexusflow.integrations.service import rotate_all
from tests.conftest import TEST_FERNET_KEY
from tests.integration.conftest import (
    create_user,
    fetch_all,
    integration_settings,
    login_headers,
    run_db,
)
from tests.support.engine_harness import Harness, engine_harness
from tests.support.partner import serve_partner

pytestmark = pytest.mark.integration

TOKEN = "tok_live_SUPERSECRETO_9f8e7d6c"
API = "/api/v1/credentials"


@pytest.fixture
def editor(client: TestClient) -> dict[str, str]:
    create_user("cred-editor@example.com", Role.EDITOR)
    return login_headers(client, "cred-editor@example.com")


def _create(client: TestClient, headers: dict[str, str], **overrides: Any) -> Any:
    body = {
        "name": "crm",
        "type": "bearer_token",
        "description": "CRM de pruebas",
        "allowed_hosts": ["127.0.0.1"],
        "secret": {"token": TOKEN},
        **overrides,
    }
    return client.post(API, json=body, headers=headers)


# ------------------------------------------------------------------ API


def test_secret_is_write_only(client: TestClient, editor: dict[str, str]) -> None:
    created = _create(client, editor)
    assert created.status_code == 201
    assert TOKEN not in created.text
    assert created.json()["hint"] == "…7d6c"
    cid = created.json()["id"]
    assert TOKEN not in client.get(API, headers=editor).text
    assert TOKEN not in client.get(f"{API}/{cid}", headers=editor).text

    stored = fetch_all("SELECT secret_encrypted, key_fingerprint FROM credentials")[0]
    assert TOKEN not in stored["secret_encrypted"]
    assert stored["key_fingerprint"] == CredentialCipher(TEST_FERNET_KEY).primary_fingerprint
    audit = fetch_all("SELECT details::text AS d FROM audit_log WHERE action LIKE 'credential.%'")
    assert all(TOKEN not in row["d"] for row in audit)


@pytest.mark.parametrize("role", [Role.VIEWER, Role.OPERATOR])
def test_only_editors_and_admins_manage_credentials(client: TestClient, role: Role) -> None:
    create_user("nope@example.com", role)
    headers = login_headers(client, "nope@example.com")
    assert client.get(API, headers=headers).status_code == 403
    assert _create(client, headers).status_code == 403


def test_invalid_secret_does_not_echo_value(client: TestClient, editor: dict[str, str]) -> None:
    response = _create(
        client, editor, type="basic_auth", secret={"username": "a:b", "password": TOKEN}
    )
    assert response.status_code == 422
    assert TOKEN not in response.text


def test_duplicate_and_update(client: TestClient, editor: dict[str, str]) -> None:
    cid = _create(client, editor).json()["id"]
    assert _create(client, editor).status_code == 409

    patched = client.patch(
        f"{API}/{cid}", json={"allowed_hosts": ["api.example.com"]}, headers=editor
    )
    assert patched.json()["allowed_hosts"] == ["api.example.com"]

    replaced = client.put(
        f"{API}/{cid}/secret",
        json={"secret": {"token": "otro-token-de-prueba-1234"}},
        headers=editor,
    )
    assert replaced.status_code == 200
    assert replaced.json()["hint"] == "…1234"
    assert replaced.json()["secret_updated_at"] is not None


def test_publish_requires_existing_credentials(client: TestClient, editor: dict[str, str]) -> None:
    definition = {
        "start": "call",
        "steps": [
            {
                "id": "call",
                "type": "http_request",
                "config": {"method": "GET", "url": "https://127.0.0.1/x", "credential": "fantasma"},
            }
        ],
    }
    validated = client.post(
        "/api/v1/workflows/validate", json={"definition": definition}, headers=editor
    ).json()
    assert [i["code"] for i in validated["issues"]] == ["unknown_credential"]

    wid = client.post(
        "/api/v1/workflows",
        json={"name": "con-credencial", "definition": definition},
        headers=editor,
    ).json()["id"]
    refused = client.post(
        f"/api/v1/workflows/{wid}/publish", json={"expected_revision": 1}, headers=editor
    )
    assert refused.status_code == 422

    _create(client, editor, name="fantasma")
    ok = client.post(
        f"/api/v1/workflows/{wid}/publish", json={"expected_revision": 1}, headers=editor
    )
    assert ok.status_code == 200


def test_credential_in_use_cannot_be_deleted(client: TestClient, editor: dict[str, str]) -> None:
    cid = _create(client, editor).json()["id"]
    definition = {
        "start": "call",
        "steps": [
            {
                "id": "call",
                "type": "http_request",
                "config": {"method": "GET", "url": "https://127.0.0.1/x", "credential": "crm"},
            }
        ],
    }
    wid = client.post(
        "/api/v1/workflows", json={"name": "usa-crm", "definition": definition}, headers=editor
    ).json()["id"]
    client.post(f"/api/v1/workflows/{wid}/publish", json={"expected_revision": 1}, headers=editor)

    blocked = client.delete(f"{API}/{cid}", headers=editor)
    assert blocked.status_code == 409
    assert blocked.json()["workflows"] == ["usa-crm"]

    client.patch(f"/api/v1/workflows/{wid}", json={"status": "archived"}, headers=editor)
    assert client.delete(f"{API}/{cid}", headers=editor).status_code == 204


# ------------------------------------------------------------------ motor


@pytest.fixture(scope="module")
def partner_url() -> Iterator[str]:
    with serve_partner() as url:
        yield url


@pytest.fixture
async def h(partner_url: str) -> AsyncIterator[Harness]:
    settings = integration_settings(
        egress_allow_http=True, egress_allowed_private_hosts=["127.0.0.1"]
    )
    async with engine_harness(settings) as harness:
        yield harness


async def _store_credential(
    h: Harness, name: str, hosts: list[str], token: str = TOKEN
) -> uuid.UUID:
    cipher = CredentialCipher(TEST_FERNET_KEY)
    cid = uuid.uuid4()
    async with h.sessions() as s:
        s.add(
            Credential(
                id=cid,
                name=name,
                type=CredentialType.BEARER_TOKEN,
                allowed_hosts=hosts,
                secret_encrypted=cipher.encrypt(cid, name, {"token": token}),
                key_fingerprint=cipher.primary_fingerprint,
                hint="…",
            )
        )
        await s.commit()
    return cid


def _echo_with(partner_url: str, credential: str, host_url: str | None = None) -> dict[str, Any]:
    return {
        "start": "call",
        "steps": [
            {
                "id": "call",
                "type": "http_request",
                "config": {
                    "method": "GET",
                    "url": host_url or f"{partner_url}/echo",
                    "credential": credential,
                    "headers": {"authorization": "Bearer intento-de-sobrescribir"},
                },
            }
        ],
    }


async def test_credential_injected_and_redacted_from_output(h: Harness, partner_url: str) -> None:
    await _store_credential(h, "crm", ["127.0.0.1"])
    wid = await h.publish(_echo_with(partner_url, "crm"))
    run_id = await h.start(wid)
    assert await h.drain() == ["succeeded"]

    output = (await h.steps(run_id))["call"].output
    assert output is not None
    # El servidor recibió el token real (la cabecera del flujo no pudo sobrescribirlo)...
    assert output["body"]["headers"]["authorization"] == "Bearer [REDACTED]"
    # ...pero no se guardó en la base de datos aunque la API lo devolviera.
    rows = await h.fetch("SELECT output::text AS o FROM step_runs")
    assert all(TOKEN not in row["o"] for row in rows)
    last_used = (await h.fetch("SELECT last_used_at FROM credentials"))[0]["last_used_at"]
    assert last_used is not None


async def test_credential_cannot_be_sent_to_other_hosts(h: Harness, partner_url: str) -> None:
    await _store_credential(h, "crm", ["api.example.com"])
    wid = await h.publish(_echo_with(partner_url, "crm"))
    run_id = await h.start(wid)
    assert await h.drain() == ["failed"]
    run = await h.run(run_id)
    assert run.error is not None
    assert run.error["class"] == "credential_host_not_allowed"


async def test_swapped_ciphertext_is_rejected(h: Harness, partner_url: str) -> None:
    await _store_credential(h, "crm", ["127.0.0.1"])
    await _store_credential(h, "otra", ["127.0.0.1"], token="token-de-otra-cuenta-0000")
    # Un atacante con escritura en la tabla copia el secreto de "otra" en "crm".
    await h.sql(
        "UPDATE credentials SET secret_encrypted = "
        "(SELECT secret_encrypted FROM credentials WHERE name = 'otra') WHERE name = 'crm'"
    )
    wid = await h.publish(_echo_with(partner_url, "crm"))
    run_id = await h.start(wid)
    assert await h.drain() == ["failed"]
    assert (await h.run(run_id)).error["class"] == "credential_unreadable"  # type: ignore[index]


async def test_missing_credential_at_runtime(h: Harness, partner_url: str) -> None:
    await _store_credential(h, "crm", ["127.0.0.1"])
    wid = await h.publish(_echo_with(partner_url, "crm"))
    await h.sql("DELETE FROM credentials")  # borrado fuera de la API
    run_id = await h.start(wid)
    assert await h.drain() == ["failed"]
    assert (await h.run(run_id)).status == RunStatus.FAILED


def test_rotate_all_reencrypts_with_new_primary_key() -> None:
    new_key = Fernet.generate_key().decode()

    async def scenario(session: AsyncSession) -> tuple[int, str]:
        old = CredentialCipher(TEST_FERNET_KEY)
        cid = uuid.uuid4()
        session.add(
            Credential(
                id=cid,
                name="rot",
                type=CredentialType.BEARER_TOKEN,
                allowed_hosts=["a.example.com"],
                secret_encrypted=old.encrypt(cid, "rot", {"token": TOKEN}),
                key_fingerprint=old.primary_fingerprint,
                hint="…",
            )
        )
        await session.commit()
        count = await rotate_all(session, CredentialCipher(f"{new_key},{TEST_FERNET_KEY}"))
        row = await session.get(Credential, cid, populate_existing=True)
        assert row is not None
        # Solo con la clave nueva ya se puede leer.
        assert CredentialCipher(new_key).decrypt(cid, "rot", row.secret_encrypted) == {
            "token": TOKEN
        }
        return count, row.key_fingerprint

    count, fingerprint = run_db(scenario)
    assert count == 1
    assert fingerprint == CredentialCipher(new_key).primary_fingerprint
