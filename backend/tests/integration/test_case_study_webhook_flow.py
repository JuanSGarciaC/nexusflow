"""Caso de estudio de punta a punta (docs/caso-de-estudio-webhook.md), en una sola prueba:
webhook firmado → ejecución → API externa responde 503 → reintento → éxito →
reentrega del mismo evento sin duplicar nada → rastro en auditoría e historial."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexusflow.auth.models import User
from nexusflow.auth.passwords import hash_password
from nexusflow.auth.permissions import Role
from nexusflow.engine.models import RunStatus
from nexusflow.main import create_app
from nexusflow.webhooks import signing
from tests.integration.conftest import STRONG_PASSWORD, integration_settings, login_headers
from tests.support.engine_harness import Harness, engine_harness
from tests.support.partner import STATE, serve_partner

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def partner_url() -> Iterator[str]:
    with serve_partner() as url:
        yield url


@pytest.fixture
async def h(partner_url: str) -> AsyncIterator[Harness]:
    STATE.reset()
    settings = integration_settings(
        egress_allow_http=True, egress_allowed_private_hosts=["127.0.0.1"]
    )
    async with engine_harness(settings) as harness:
        yield harness


async def test_signed_webhook_survives_503_and_redelivery(h: Harness, partner_url: str) -> None:
    definition = {
        "inputs": [{"name": "order_id", "type": "string"}],
        "start": "notify_partner",
        "steps": [
            {
                "id": "notify_partner",
                "type": "http_request",
                # El socio falla con 503 la primera vez y responde 200 la segunda.
                "config": {"method": "GET", "url": f"{partner_url}/flaky/order?fail=1&code=503"},
                "retry": {"max_attempts": 3, "initial_backoff_seconds": 0.1},
                "timeout_seconds": 5,
            }
        ],
    }
    # La API comparte el dispatcher del arnés: lo que encola el webhook lo procesa el "worker".
    with TestClient(
        create_app(h.settings, dispatcher=h.dispatcher), base_url="https://testserver"
    ) as api:
        async with h.sessions() as s:  # create_user() usa asyncio.run: no vale aquí
            s.add(
                User(
                    email="caso@example.com",
                    password_hash=hash_password(STRONG_PASSWORD),
                    role=Role.EDITOR,
                )
            )
            await s.commit()
        editor = login_headers(api, "caso@example.com")
        wid = api.post(
            "/api/v1/workflows",
            json={"name": "caso-estudio", "definition": definition},
            headers=editor,
        ).json()["id"]
        api.post(f"/api/v1/workflows/{wid}/publish", json={"expected_revision": 1}, headers=editor)
        trigger = api.post(
            f"/api/v1/workflows/{wid}/triggers", json={"name": "erp"}, headers=editor
        ).json()

        body = json.dumps({"order_id": "PED-1001"}).encode()

        def deliver(secret: str) -> Any:
            return api.post(
                trigger["url_path"],
                content=body,
                headers={
                    "Content-Type": "application/json",
                    signing.SIGNATURE_HEADER: signing.sign(secret, body),
                    signing.EVENT_ID_HEADER: "evt_caso_1001",
                },
            )

        # 1. Autenticación: una firma con otro secreto no crea nada.
        assert deliver("whsec_" + "x" * 40).status_code == 401
        assert await h.fetch("SELECT id FROM workflow_runs") == []

        # 2. Webhook firmado → ejecución aceptada (202) y encolada.
        first = deliver(trigger["secret"])
        assert first.status_code == 202, first.text
        run_id = first.json()["run_id"]

        # 3-4. El worker procesa: 503 → reintento programado → éxito.
        assert await h.drain() == ["retry_scheduled", "succeeded"]

        # 5. El ERP reenvía el mismo evento: misma ejecución, ninguna llamada nueva al socio.
        again = deliver(trigger["secret"])
        assert again.status_code == 200
        assert again.json() == {"run_id": run_id, "duplicate": True}
        assert await h.drain() == []

    assert STATE.calls["flaky:order"] == 2
    run = await h.run(run_id)
    assert run.status == RunStatus.SUCCEEDED
    assert run.trigger_type == "webhook"

    # 6. Historial de intentos y auditoría.
    step = (await h.steps(run.id))["notify_partner"]
    attempts = await h.attempts(step.id)
    assert [(a.outcome, a.http_status) for a in attempts] == [("failed", 503), ("succeeded", None)]
    created = await h.fetch("SELECT resource_id FROM audit_log WHERE action = 'run.created'")
    assert created == [{"resource_id": run_id}]  # una sola, pese a la reentrega
