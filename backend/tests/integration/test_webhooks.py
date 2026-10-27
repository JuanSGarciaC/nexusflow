"""Webhooks entrantes (firma, anti-replay, deduplicación) y salientes (outbox, entrega
firmada, reintentos, dead-letter) contra PostgreSQL, Redis y un receptor HTTP reales."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from nexusflow.audit.service import RequestContext
from nexusflow.auth.models import User
from nexusflow.auth.permissions import Role
from nexusflow.engine.dispatch import DELIVER_WEBHOOK_TASK, RecordingDispatcher
from nexusflow.engine.models import RunStatus, WorkflowRun
from nexusflow.integrations.crypto import CredentialCipher
from nexusflow.main import create_app
from nexusflow.webhooks import service as webhooks
from nexusflow.webhooks import signing
from nexusflow.webhooks.models import DeliveryStatus, WebhookDelivery
from nexusflow.webhooks.schemas import SubscriptionCreate
from tests.conftest import TEST_FERNET_KEY
from tests.integration.conftest import (
    create_user,
    fetch_all,
    integration_settings,
    login_headers,
)
from tests.support.engine_harness import Harness, engine_harness
from tests.support.partner import RECEIVER_SECRET, STATE, serve_partner

pytestmark = pytest.mark.integration

DEFINITION: dict[str, Any] = {
    "inputs": [
        {"name": "order_id", "type": "string"},
        {"name": "amount", "type": "number", "required": False},
    ],
    "start": "echo",
    "steps": [
        {"id": "echo", "type": "transform", "config": {"output": {"order": "{{ input.order_id }}"}}}
    ],
}


# ------------------------------------------------------------------ entrantes


@pytest.fixture
def dispatcher() -> RecordingDispatcher:
    return RecordingDispatcher()


@pytest.fixture
def api_factory(dispatcher: RecordingDispatcher) -> Iterator[Callable[..., TestClient]]:
    opened: list[TestClient] = []

    def factory(**overrides: object) -> TestClient:
        client = TestClient(
            create_app(integration_settings(**overrides), dispatcher=dispatcher),
            base_url="https://testserver",
        )
        client.__enter__()
        opened.append(client)
        return client

    yield factory
    for client in opened:
        client.__exit__(None, None, None)


@pytest.fixture
def api(api_factory: Callable[..., TestClient]) -> TestClient:
    return api_factory()


@pytest.fixture
def editor(api: TestClient) -> dict[str, str]:
    create_user("hooks@example.com", Role.EDITOR)
    return login_headers(api, "hooks@example.com")


def _workflow(api: TestClient, headers: dict[str, str]) -> str:
    wid: str = api.post(
        "/api/v1/workflows",
        json={"name": f"wh-{uuid.uuid4().hex[:6]}", "definition": DEFINITION},
        headers=headers,
    ).json()["id"]
    api.post(f"/api/v1/workflows/{wid}/publish", json={"expected_revision": 1}, headers=headers)
    return wid


def _trigger(api: TestClient, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    wid = _workflow(api, headers)
    response = api.post(
        f"/api/v1/workflows/{wid}/triggers", json={"name": "erp", **body}, headers=headers
    )
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


def _send(
    api: TestClient,
    trigger: dict[str, Any],
    payload: Any,
    *,
    secret: str | None = None,
    timestamp: int | None = None,
    event_id: str | None = None,
    raw: bytes | None = None,
) -> Any:
    body = raw if raw is not None else json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        signing.SIGNATURE_HEADER: signing.sign(secret or trigger["secret"], body, timestamp),
    }
    if event_id:
        headers[signing.EVENT_ID_HEADER] = event_id
    return api.post(trigger["url_path"], content=body, headers=headers)


def test_trigger_secret_shown_once(api: TestClient, editor: dict[str, str]) -> None:
    trigger = _trigger(api, editor)
    assert trigger["secret"].startswith("whsec_")
    assert trigger["url_path"] == f"/api/v1/hooks/{trigger['id']}"
    listing = api.get(f"/api/v1/workflows/{trigger['workflow_id']}/triggers", headers=editor)
    assert trigger["secret"] not in listing.text
    stored = fetch_all("SELECT secret_encrypted FROM webhook_triggers")[0]["secret_encrypted"]
    assert trigger["secret"] not in stored


@pytest.mark.parametrize("role", [Role.VIEWER, Role.OPERATOR])
def test_trigger_management_requires_permission(api: TestClient, role: Role) -> None:
    create_user("x@example.com", role)
    headers = login_headers(api, "x@example.com")
    assert api.get("/api/v1/webhook-subscriptions", headers=headers).status_code == 403


def test_signed_webhook_starts_run_with_filtered_input(
    api: TestClient, editor: dict[str, str], dispatcher: RecordingDispatcher
) -> None:
    trigger = _trigger(api, editor)
    dispatcher.drain()
    payload = {"order_id": "A-77", "amount": 12.5, "irrelevant": {"x": 1}}
    response = _send(api, trigger, payload, event_id="evt_001")
    assert response.status_code == 202, response.text
    assert response.json()["duplicate"] is False

    run = api.get(f"/api/v1/runs/{response.json()['run_id']}", headers=editor).json()
    assert run["trigger_type"] == "webhook"
    assert run["triggered_by"] is None
    assert run["input"] == {"order_id": "A-77", "amount": 12.5}  # campos extra ignorados
    assert len(dispatcher.drain()) == 1


def test_duplicate_events_do_not_create_new_runs(api: TestClient, editor: dict[str, str]) -> None:
    trigger = _trigger(api, editor)
    first = _send(api, trigger, {"order_id": "A-1"}, event_id="evt_dup")
    second = _send(api, trigger, {"order_id": "A-1"}, event_id="evt_dup")
    assert (first.status_code, second.status_code) == (202, 200)
    assert second.json() == {"run_id": first.json()["run_id"], "duplicate": True}

    # Sin id de evento: una repetición exacta (mismo t y cuerpo) también se detecta.
    ts = int(time.time())
    a = _send(api, trigger, {"order_id": "B-2"}, timestamp=ts)
    b = _send(api, trigger, {"order_id": "B-2"}, timestamp=ts)
    assert b.json()["duplicate"] is True
    assert a.json()["run_id"] == b.json()["run_id"]
    assert len(fetch_all("SELECT id FROM workflow_runs")) == 2


def test_rejected_signatures(api: TestClient, editor: dict[str, str]) -> None:
    trigger = _trigger(api, editor)
    url = trigger["url_path"]
    body = json.dumps({"order_id": "A-1"}).encode()

    assert api.post(url, content=body).status_code == 401  # sin firma
    assert _send(api, trigger, {"order_id": "A-1"}, secret="whsec_" + "x" * 40).status_code == 401
    stale = _send(api, trigger, {"order_id": "A-1"}, timestamp=int(time.time()) - 600)
    assert stale.status_code == 401
    # Firma válida de un cuerpo, enviada con otro cuerpo (manipulación en tránsito).
    header = signing.sign(trigger["secret"], body)
    tampered = api.post(
        url, content=b'{"order_id":"A-2"}', headers={signing.SIGNATURE_HEADER: header}
    )
    assert tampered.status_code == 401
    assert fetch_all("SELECT id FROM workflow_runs") == []


def test_payload_and_input_validation(api: TestClient, editor: dict[str, str]) -> None:
    trigger = _trigger(api, editor)
    assert _send(api, trigger, None, raw=b"no es json").status_code == 422
    assert _send(api, trigger, [1, 2, 3]).status_code == 422
    assert _send(api, trigger, {"amount": 1}).status_code == 422  # falta order_id
    assert _send(api, trigger, {"order_id": "A"}, event_id="mal id!").status_code == 422


def test_unknown_and_inactive_triggers_look_the_same(
    api: TestClient, editor: dict[str, str]
) -> None:
    trigger = _trigger(api, editor)
    unknown = api.post(f"/api/v1/hooks/{uuid.uuid4()}", content=b"{}")
    api.patch(f"/api/v1/triggers/{trigger['id']}", json={"is_active": False}, headers=editor)
    inactive = _send(api, trigger, {"order_id": "A"})
    assert unknown.status_code == inactive.status_code == 404
    assert unknown.json()["detail"] == inactive.json()["detail"]


def test_rotation_invalidates_old_secret(api: TestClient, editor: dict[str, str]) -> None:
    trigger = _trigger(api, editor)
    rotated = api.post(f"/api/v1/triggers/{trigger['id']}/rotate-secret", headers=editor).json()
    assert rotated["secret"] != trigger["secret"]
    assert _send(api, trigger, {"order_id": "A"}).status_code == 401
    assert _send(api, trigger, {"order_id": "A"}, secret=rotated["secret"]).status_code == 202


def test_trigger_rate_limit(api: TestClient, editor: dict[str, str]) -> None:
    trigger = _trigger(api, editor, rate_limit_per_minute=2)
    codes = [_send(api, trigger, {"order_id": f"R{i}"}).status_code for i in range(3)]
    assert codes == [202, 202, 429]


def test_subscription_api(api: TestClient, editor: dict[str, str]) -> None:
    insecure = api.post(
        "/api/v1/webhook-subscriptions",
        json={"name": "x", "url": "http://hooks.example.com", "events": ["run.failed"]},
        headers=editor,
    )
    assert insecure.status_code == 422  # http no permitido fuera de desarrollo
    created = api.post(
        "/api/v1/webhook-subscriptions",
        json={"name": "ops", "url": "https://hooks.example.com/nf", "events": ["run.failed"]},
        headers=editor,
    )
    assert created.status_code == 201
    secret = created.json()["secret"]
    assert secret not in api.get("/api/v1/webhook-subscriptions", headers=editor).text
    sid = created.json()["id"]
    assert api.post(f"/api/v1/webhook-subscriptions/{sid}/ping", headers=editor).status_code == 202
    deliveries = api.get(f"/api/v1/webhook-subscriptions/{sid}/deliveries", headers=editor).json()
    assert deliveries["total"] == 1
    assert deliveries["items"][0]["event_type"] == "ping"


# ------------------------------------------------------------------ salientes (outbox)


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


async def _subscribe(
    h: Harness, url: str, events: list[str], workflow_id: uuid.UUID | None = None
) -> uuid.UUID:
    async with h.sessions() as s:
        actor = await s.get(User, h.actor_id)
        assert actor is not None
        subscription, _ = await webhooks.create_subscription(
            s,
            h.settings,
            CredentialCipher(TEST_FERNET_KEY),
            actor,
            SubscriptionCreate(
                name="receptor",
                url=url,
                events=events,
                workflow_id=workflow_id,
                secret=RECEIVER_SECRET,
            ),
            RequestContext(),
        )
        return subscription.id


async def _deliveries(h: Harness) -> list[WebhookDelivery]:
    async with h.sessions() as s:
        return list(await s.scalars(select(WebhookDelivery).order_by(WebhookDelivery.created_at)))


async def test_run_event_delivered_signed(h: Harness, partner_url: str) -> None:
    await _subscribe(h, f"{partner_url}/receiver", ["run.succeeded"])
    wid = await h.publish(DEFINITION)
    run_id = await h.start(wid, {"order_id": "Z-9"})
    labels = await h.drain()
    assert labels == ["succeeded", "webhook:delivered"]

    [event] = STATE.received  # el receptor verificó la firma antes de aceptarlo
    assert event["event_type"] == "run.succeeded"
    assert event["payload"]["type"] == "run.succeeded"
    assert event["payload"]["data"]["run_id"] == str(run_id)
    assert event["payload"]["data"]["output"] == {"order": "Z-9"}
    [delivery] = await _deliveries(h)
    assert delivery.status == DeliveryStatus.DELIVERED
    assert str(delivery.event_id) == event["event_id"]


async def test_subscription_filters(h: Harness, partner_url: str) -> None:
    other = await h.publish(DEFINITION, name="otro")
    await _subscribe(h, f"{partner_url}/receiver", ["run.failed"])  # otro evento
    await _subscribe(h, f"{partner_url}/receiver", ["run.succeeded"], workflow_id=other)
    wid = await h.publish(DEFINITION)
    await h.start(wid, {"order_id": "A"})
    await h.drain()
    assert await _deliveries(h) == []


async def test_outbox_rolls_back_with_the_transaction(h: Harness, partner_url: str) -> None:
    await _subscribe(h, f"{partner_url}/receiver", ["run.cancelled"])
    wid = await h.publish(DEFINITION)
    run_id = await h.start(wid, {"order_id": "A"})
    async with h.sessions() as s:
        run = await s.get(WorkflowRun, run_id)
        assert run is not None
        run.status = RunStatus.CANCELLED
        await s.flush()  # el hook crea la entrega en esta transacción...
        assert len(list(await s.scalars(select(WebhookDelivery)))) == 1
        await s.rollback()  # ...y desaparece con ella
    assert await _deliveries(h) == []


async def test_retries_keep_event_id_then_dead_letter_and_redeliver(
    h: Harness, partner_url: str
) -> None:
    await _subscribe(h, f"{partner_url}/receiver", ["run.succeeded"])
    wid = await h.publish(DEFINITION)
    await h.start(wid, {"order_id": "A"})
    STATE.receiver_failures = 5
    await h.drain()  # paso OK; primera entrega falla (500)
    [delivery] = await _deliveries(h)
    assert delivery.status == DeliveryStatus.RETRY_SCHEDULED
    assert delivery.last_status_code == 500
    assert delivery.next_attempt_at is not None  # backoff de ~30 s

    await h.sql("UPDATE webhook_deliveries SET max_attempts = 2, next_attempt_at = now()")
    assert await h.deliverer.deliver(delivery.id) == "dead"

    STATE.receiver_failures = 0
    async with h.sessions() as s:
        actor = await s.get(User, h.actor_id)
        assert actor is not None
        _, item = await webhooks.requeue_delivery(s, actor, delivery.id, RequestContext())
    assert item.task == DELIVER_WEBHOOK_TASK
    assert await h.deliverer.deliver(delivery.id) == "delivered"
    assert [e["event_id"] for e in STATE.received] == [str(delivery.event_id)]


async def test_ssrf_protection_applies_to_deliveries(h: Harness) -> None:
    await _subscribe(h, "http://169.254.169.254/latest/meta-data", ["run.succeeded"])
    wid = await h.publish(DEFINITION)
    await h.start(wid, {"order_id": "A"})
    await h.drain()
    [delivery] = await _deliveries(h)
    assert delivery.status == DeliveryStatus.RETRY_SCHEDULED
    assert delivery.last_error is not None
    assert "egress_blocked" in delivery.last_error


async def test_sweep_recovers_lost_deliveries(h: Harness, partner_url: str) -> None:
    await _subscribe(h, f"{partner_url}/receiver", ["run.succeeded"])
    wid = await h.publish(DEFINITION)
    await h.start(wid, {"order_id": "A"})
    for item in h.dispatcher.drain():  # solo el paso; la entrega "se pierde"
        await h.engine.process_step(item.step_run_id)
    h.dispatcher.drain()
    await h.sql(
        "UPDATE webhook_deliveries SET status = 'delivering', "
        "lease_expires_at = now() - interval '1 second'"
    )
    dispatched, recovered = await h.deliverer.sweep()
    assert (dispatched, recovered) == (1, 1)
    await h.drain()
    [delivery] = await _deliveries(h)
    assert delivery.status == DeliveryStatus.DELIVERED
