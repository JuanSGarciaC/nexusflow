"""De punta a punta con piezas reales: API → Redis (broker) → worker Celery → PostgreSQL
→ API externa. Sin dispatcher simulado ni ejecución en línea."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from celery.contrib.testing.worker import start_worker
from fastapi.testclient import TestClient

from nexusflow.auth.permissions import Role
from nexusflow.engine.worker import build_worker_app
from nexusflow.main import create_app
from tests.integration.conftest import create_user, integration_settings, login_headers, redis_url
from tests.support.partner import serve_partner

pytestmark = pytest.mark.integration


def _broker_url() -> str:
    # Base de Redis distinta a la de las pruebas (la 15 se vacía entre pruebas).
    return redis_url().rsplit("/", 1)[0] + "/14"


@pytest.fixture
def partner_url() -> Iterator[str]:
    with serve_partner() as url:
        yield url


def test_run_executes_through_real_celery_worker(partner_url: str) -> None:
    settings = integration_settings(
        celery_broker_url=_broker_url(),
        egress_allow_http=True,
        egress_allowed_private_hosts=["127.0.0.1"],
    )
    worker_app, _ = build_worker_app(settings)
    create_user("e2e@example.com", Role.ADMIN)
    definition = {
        "start": "lookup",
        "steps": [
            {
                "id": "lookup",
                "type": "http_request",
                "config": {"method": "GET", "url": f"{partner_url}/customers/vip-1"},
                "next": "pause",
            },
            {"id": "pause", "type": "delay", "config": {"seconds": 1}, "next": "done"},
            {
                "id": "done",
                "type": "transform",
                "config": {"output": {"tier": "{{ steps.lookup.output.body.tier }}"}},
            },
        ],
    }

    with (
        start_worker(worker_app, pool="solo", perform_ping_check=False, shutdown_timeout=10),
        TestClient(create_app(settings), base_url="https://testserver") as api,
    ):
        headers = login_headers(api, "e2e@example.com")
        wid = api.post(
            "/api/v1/workflows", json={"name": "E2E", "definition": definition}, headers=headers
        ).json()["id"]
        api.post(f"/api/v1/workflows/{wid}/publish", json={"expected_revision": 1}, headers=headers)
        run_id = api.post(f"/api/v1/workflows/{wid}/runs", json={}, headers=headers).json()["id"]

        deadline = time.monotonic() + 30
        body: dict[str, object] = {}
        while time.monotonic() < deadline:
            body = api.get(f"/api/v1/runs/{run_id}", headers=headers).json()
            if body["status"] not in ("pending", "running"):
                break
            time.sleep(0.2)

    assert body["status"] == "succeeded", body
    assert body["output"] == {"tier": "vip"}
    steps = body["steps"]
    assert isinstance(steps, list)
    assert [s["step_id"] for s in steps] == ["lookup", "pause", "done"]
    worker_app.nexusflow_close()
