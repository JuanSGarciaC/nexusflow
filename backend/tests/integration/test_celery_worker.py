"""De punta a punta con piezas reales: API → Redis (broker) → worker Celery → PostgreSQL
→ API externa. Sin dispatcher simulado ni ejecución en línea."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator

import pytest
from celery.contrib.testing.worker import start_worker
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

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


def test_due_schedule_runs_through_the_real_sweep_task() -> None:
    """beat → tarea de barrido → ScheduleFirer → ejecución → worker → resultado."""
    from datetime import timedelta

    from sqlalchemy import update

    from nexusflow.db.types import utcnow
    from nexusflow.engine.dispatch import SWEEP_TASK
    from nexusflow.schedules.models import WorkflowSchedule
    from tests.integration.conftest import run_db

    settings = integration_settings(celery_broker_url=_broker_url())
    worker_app, _ = build_worker_app(settings)
    create_user("cron-e2e@example.com", Role.ADMIN)
    definition = {
        "inputs": [{"name": "region", "type": "string"}],
        "start": "done",
        "steps": [
            {"id": "done", "type": "transform", "config": {"output": {"r": "{{ input.region }}"}}}
        ],
    }

    with (
        start_worker(worker_app, pool="solo", perform_ping_check=False, shutdown_timeout=10),
        TestClient(create_app(settings), base_url="https://testserver") as api,
    ):
        headers = login_headers(api, "cron-e2e@example.com")
        wid = api.post(
            "/api/v1/workflows", json={"name": "Cron", "definition": definition}, headers=headers
        ).json()["id"]
        api.post(f"/api/v1/workflows/{wid}/publish", json={"expected_revision": 1}, headers=headers)
        schedule = api.post(
            f"/api/v1/workflows/{wid}/schedules",
            json={"name": "cada 10", "cron_expression": "*/10 * * * *", "input": {"region": "eu"}},
            headers=headers,
        ).json()

        async def _make_due(session: AsyncSession) -> None:
            await session.execute(
                update(WorkflowSchedule)
                .where(WorkflowSchedule.id == uuid.UUID(schedule["id"]))
                .values(next_run_at=utcnow() - timedelta(seconds=20))
            )
            await session.commit()

        run_db(_make_due)
        worker_app.send_task(SWEEP_TASK)

        deadline = time.monotonic() + 30
        runs: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            runs = api.get(f"/api/v1/runs?workflow_id={wid}", headers=headers).json()["items"]
            if runs and runs[0]["status"] == "succeeded":
                break
            time.sleep(0.3)

    assert len(runs) == 1, runs
    assert runs[0]["status"] == "succeeded"
    assert runs[0]["trigger_type"] == "schedule"
    worker_app.nexusflow_close()
