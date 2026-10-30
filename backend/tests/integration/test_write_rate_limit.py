"""Límites de escritura por usuario en la API de gestión (PostgreSQL y Redis reales)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexusflow.auth.permissions import Role
from nexusflow.engine.dispatch import RecordingDispatcher
from nexusflow.main import create_app
from tests.integration.conftest import create_user, integration_settings, login_headers

pytestmark = pytest.mark.integration

DEFINITION: dict[str, Any] = {
    "inputs": [],
    "start": "t",
    "steps": [{"id": "t", "type": "transform", "config": {"output": {"ok": "1"}}}],
}


@pytest.fixture
def dispatcher() -> RecordingDispatcher:
    return RecordingDispatcher()


def _client(dispatcher: RecordingDispatcher, **limits: int) -> TestClient:
    app = create_app(integration_settings(**limits), dispatcher=dispatcher)
    return TestClient(app, base_url="https://testserver")


def _create_workflow(api: TestClient, headers: dict[str, str]) -> int:
    # Nombre único: repetirlo daría 409, que también cuenta para el límite pero ensucia la prueba.
    body = {"name": f"Flujo {uuid.uuid4().hex[:8]}", "definition": DEFINITION}
    return api.post("/api/v1/workflows", json=body, headers=headers).status_code


def test_writes_are_limited_per_user_and_reads_are_not(dispatcher: RecordingDispatcher) -> None:
    with _client(dispatcher, write_rate_limit_per_user=3) as api:
        create_user("w1@example.com", Role.EDITOR)
        create_user("w2@example.com", Role.EDITOR)
        first = login_headers(api, "w1@example.com")
        codes = [_create_workflow(api, first) for _ in range(4)]
        assert codes == [201, 201, 201, 429]
        # Las lecturas no cuentan: se puede seguir consultando con el límite agotado.
        assert api.get("/api/v1/workflows", headers=first).status_code == 200
        # El límite es por usuario: otro editor no se ve afectado.
        assert _create_workflow(api, login_headers(api, "w2@example.com")) == 201


def test_rate_limited_write_returns_problem_with_retry_after(
    dispatcher: RecordingDispatcher,
) -> None:
    with _client(dispatcher, write_rate_limit_per_user=1) as api:
        create_user("w3@example.com", Role.EDITOR)
        headers = login_headers(api, "w3@example.com")
        assert _create_workflow(api, headers) == 201
        body = {"name": "Otro flujo", "definition": DEFINITION}
        blocked = api.post("/api/v1/workflows", json=body, headers=headers)
        assert blocked.status_code == 429
        assert blocked.headers["content-type"].startswith("application/problem+json")
        assert int(blocked.headers["retry-after"]) >= 1


def test_unauthenticated_write_is_401_not_429(dispatcher: RecordingDispatcher) -> None:
    with _client(dispatcher, write_rate_limit_per_user=1) as api:
        codes = [
            api.post("/api/v1/workflows", json={"name": "x", "definition": DEFINITION}).status_code
            for _ in range(3)
        ]
        # Sin usuario no hay clave que contar: la autenticación falla antes que el límite.
        assert codes == [401, 401, 401]


def test_webhook_ping_has_its_own_stricter_limit(dispatcher: RecordingDispatcher) -> None:
    with _client(dispatcher, webhook_dispatch_rate_limit_per_user=2) as api:
        create_user("w4@example.com", Role.EDITOR)
        headers = login_headers(api, "w4@example.com")
        created = api.post(
            "/api/v1/webhook-subscriptions",
            json={"name": "ops", "url": "https://hooks.example.com/nf", "events": ["run.failed"]},
            headers=headers,
        )
        assert created.status_code == 201
        url = f"/api/v1/webhook-subscriptions/{created.json()['id']}/ping"
        codes = [api.post(url, headers=headers).status_code for _ in range(3)]
        assert codes == [202, 202, 429]
