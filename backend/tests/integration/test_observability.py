"""Fase 7: métricas de ejecución, /metrics (Prometheus) y consulta de auditoría."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexusflow.auth.permissions import Role
from nexusflow.engine.dispatch import RecordingDispatcher
from nexusflow.engine.models import RunStatus
from nexusflow.main import create_app
from nexusflow.observability.analytics import summarize
from tests.integration.conftest import create_user, integration_settings, login_headers
from tests.support.engine_harness import Harness, engine_harness
from tests.support.partner import STATE, serve_partner

pytestmark = pytest.mark.integration

METRICS_TOKEN = "prometheus-scrape-token-0123456789"


def transform_flow() -> dict[str, Any]:
    return {
        "start": "calc",
        "steps": [{"id": "calc", "type": "transform", "config": {"output": {"ok": True}}}],
    }


def http_flow(url: str, attempts: int) -> dict[str, Any]:
    return {
        "start": "call",
        "steps": [
            {
                "id": "call",
                "type": "http_request",
                "config": {"method": "GET", "url": url},
                "retry": {"max_attempts": attempts, "initial_backoff_seconds": 0.1},
                "timeout_seconds": 5,
            }
        ],
    }


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


async def _summary(h: Harness, **kwargs: Any) -> Any:
    async with h.sessions() as s:
        return await summarize(s, **{"window_hours": 24, **kwargs})


# ------------------------------------------------------------------ métricas de negocio


async def test_summary_reflects_real_executions(h: Harness, partner_url: str) -> None:
    ok = await h.publish(transform_flow(), name="facturas")
    flaky = await h.publish(http_flow(f"{partner_url}/flaky/m?fail=9", attempts=2), name="crm")
    missing = await h.publish(http_flow(f"{partner_url}/status/404", attempts=3), name="erp")
    for _ in range(3):
        await h.start(ok)
    flaky_run = await h.start(flaky)
    await h.start(missing)
    await h.drain()

    m = await _summary(h)
    assert m.total_runs == 5
    assert m.runs_by_status["succeeded"] == 3
    assert m.runs_by_status["failed"] == 2
    assert m.success_rate == 0.6
    assert m.run_duration.count == 5
    assert m.run_duration.p50_ms is not None
    assert m.run_duration.p95_ms >= m.run_duration.p50_ms
    # flaky: 2 intentos (1 reintento); 404 permanente: 1 intento; 3 transform: 1 cada uno.
    assert m.attempts == 6
    assert m.retries == 1
    assert sum(b.total for b in m.timeline) == 5
    assert {t.step_type for t in m.step_types} == {"transform", "http_request"}
    http_stats = next(t for t in m.step_types if t.step_type == "http_request")
    assert http_stats.attempts == 3
    assert http_stats.failed_attempts == 3
    assert "http_404" in {e.error_class for e in m.error_classes}
    assert {w.name for w in m.top_failing_workflows} == {"crm", "erp"}
    assert len(m.recent_failures) == 2
    assert all(f.step_id == "call" for f in m.recent_failures)
    assert flaky_run in {f.run_id for f in m.recent_failures}


async def test_percentiles_are_exact(h: Harness) -> None:
    wid = await h.publish(transform_flow())
    ids = [await h.start(wid) for _ in range(4)]
    await h.drain()
    # Duraciones conocidas: 100, 200, 300 y 400 ms.
    for run_id, ms in zip(ids, (100, 200, 300, 400), strict=True):
        await h.sql(
            "UPDATE workflow_runs SET finished_at = started_at + make_interval(secs => :s) "
            "WHERE id = :id",
            s=ms / 1000,
            id=run_id,
        )
    d = (await _summary(h)).run_duration
    assert d.count == 4
    assert d.avg_ms == 250.0
    assert d.p50_ms == 250.0  # percentile_cont interpola entre 200 y 300
    assert d.p95_ms == 385.0
    assert d.max_ms == 400.0


async def test_window_and_workflow_filters(h: Harness) -> None:
    a = await h.publish(transform_flow())
    b = await h.publish(transform_flow())
    old = await h.start(a)
    await h.start(a)
    await h.start(b)
    await h.drain()
    await h.sql(
        "UPDATE workflow_runs SET created_at = now() - interval '3 days', "
        "started_at = now() - interval '3 days', finished_at = now() - interval '3 days' "
        "WHERE id = :id",
        id=old,
    )
    assert (await _summary(h)).total_runs == 2
    wide = await _summary(h, window_hours=96)
    assert wide.total_runs == 3
    assert wide.bucket == "day"
    assert len(wide.timeline) in (4, 5)  # días tocados por la ventana de 96 h
    only_a = await _summary(h, window_hours=96, workflow_id=a)
    assert only_a.total_runs == 2
    assert (await _summary(h, workflow_id=uuid.uuid4())).total_runs == 0


async def test_empty_window_has_no_success_rate(h: Harness) -> None:
    m = await _summary(h)
    assert m.total_runs == 0
    assert m.success_rate is None
    assert m.run_duration.p95_ms is None
    assert len(m.timeline) in (24, 25)
    assert all(b.total == 0 for b in m.timeline)


async def test_cancelled_runs_do_not_count_as_failures(h: Harness) -> None:
    wid = await h.publish(transform_flow())
    run_id = await h.start(wid)
    await h.sql("UPDATE workflow_runs SET status = 'cancelled' WHERE id = :id", id=run_id)
    m = await _summary(h)
    assert m.runs_by_status[RunStatus.CANCELLED.value] == 1
    assert m.success_rate is None


# ------------------------------------------------------------------ API


@pytest.fixture
def api() -> Iterator[TestClient]:
    app = create_app(
        integration_settings(metrics_token=METRICS_TOKEN), dispatcher=RecordingDispatcher()
    )
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def test_metrics_summary_permissions_and_validation(api: TestClient) -> None:
    create_user("viewer@example.com", Role.VIEWER)
    viewer = login_headers(api, "viewer@example.com")
    assert api.get("/api/v1/metrics/summary").status_code == 401
    ok = api.get("/api/v1/metrics/summary", headers=viewer)
    assert ok.status_code == 200
    assert ok.json()["total_runs"] == 0
    for bad in (0, 721):
        response = api.get("/api/v1/metrics/summary", params={"window_hours": bad}, headers=viewer)
        assert response.status_code == 422


def test_prometheus_requires_token(api: TestClient) -> None:
    assert api.get("/metrics").status_code == 401
    wrong = api.get("/metrics", headers={"Authorization": "Bearer otro-token-cualquiera-123456"})
    assert wrong.status_code == 401


def test_prometheus_exposes_http_and_business_metrics(api: TestClient) -> None:
    create_user("reader@example.com", Role.VIEWER)
    headers = login_headers(api, "reader@example.com")
    run_id = uuid.uuid4()
    assert api.get(f"/api/v1/runs/{run_id}", headers=headers).status_code == 404

    response = api.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    # Plantilla de ruta, no el id real: la cardinalidad no crece con los datos.
    assert 'route="/api/v1/runs/{run_id}"' in body
    assert str(run_id) not in body
    assert (
        'nexusflow_http_requests_total{method="POST",route="/api/v1/auth/login",status="200"}'
        in body
    )
    assert "nexusflow_http_request_duration_seconds_bucket" in body
    assert 'nexusflow_runs{status="succeeded"} 0.0' in body
    assert 'nexusflow_webhook_deliveries_open{status="dead"} 0.0' in body
    assert 'route="/metrics"' not in body  # el scrape no se mide a sí mismo


def test_prometheus_disabled_without_token() -> None:
    app = create_app(integration_settings(), dispatcher=RecordingDispatcher())
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/metrics").status_code == 404


# ------------------------------------------------------------------ auditoría


def test_audit_log_query_filters_and_pagination(api: TestClient) -> None:
    admin = create_user("auditor@example.com", Role.ADMIN)
    create_user("someone@example.com", Role.VIEWER)
    headers = login_headers(api, "auditor@example.com")
    for _ in range(3):
        api.post("/api/v1/auth/login", json={"email": "someone@example.com", "password": "mala"})

    viewer = login_headers(api, "someone@example.com")
    assert api.get("/api/v1/audit-logs", headers=viewer).status_code == 403

    everything = api.get("/api/v1/audit-logs", headers=headers).json()
    actions = [e["action"] for e in everything["items"]]
    # Orden descendente: el login correcto del viewer es el último evento.
    assert actions[0] == "auth.login.succeeded"
    assert actions.count("auth.login.failed") == 3
    assert everything["next_before_id"] is None

    failed = api.get(
        "/api/v1/audit-logs", params={"action": "auth.login.failed"}, headers=headers
    ).json()["items"]
    assert len(failed) == 3
    assert all(e["details"]["reason"] == "bad_password" for e in failed)
    assert all(e["ip_address"] == "127.0.0.1" or e["ip_address"] is None for e in failed)

    mine = api.get(
        "/api/v1/audit-logs", params={"actor_user_id": str(admin.id)}, headers=headers
    ).json()["items"]
    assert {e["actor_email"] for e in mine} == {"auditor@example.com"}

    # Paginación por cursor: 2 + 2 + resto, sin solapes.
    seen: list[int] = []
    cursor: int | None = None
    while True:
        params: dict[str, Any] = {"limit": 2, "action_prefix": "auth."}
        if cursor:
            params["before_id"] = cursor
        page = api.get("/api/v1/audit-logs", params=params, headers=headers).json()
        seen += [e["id"] for e in page["items"]]
        cursor = page["next_before_id"]
        if cursor is None:
            break
    assert seen == sorted(seen, reverse=True)
    assert len(seen) == len(set(seen)) == len(actions)


def test_audit_prefix_is_not_a_wildcard(api: TestClient) -> None:
    create_user("root@example.com", Role.ADMIN)
    headers = login_headers(api, "root@example.com")
    for prefix in ("%", "_"):
        page = api.get(
            "/api/v1/audit-logs", params={"action_prefix": prefix}, headers=headers
        ).json()
        assert page["items"] == []
    too_many = api.get("/api/v1/audit-logs", params={"limit": 500}, headers=headers)
    assert too_many.status_code == 422
