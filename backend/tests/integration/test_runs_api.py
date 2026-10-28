"""API de ejecuciones: permisos, idempotencia, validación de entrada, cancelación."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexusflow.auth.permissions import Role
from nexusflow.engine.dispatch import RecordingDispatcher
from nexusflow.main import create_app
from tests.integration.conftest import create_user, integration_settings, login_headers

pytestmark = pytest.mark.integration

DEFINITION: dict[str, Any] = {
    "inputs": [{"name": "amount", "type": "number"}],
    "start": "calc",
    "steps": [
        {"id": "calc", "type": "transform", "config": {"output": {"total": "{{ input.amount }}"}}}
    ],
}


@pytest.fixture
def dispatcher() -> RecordingDispatcher:
    return RecordingDispatcher()


@pytest.fixture
def api(dispatcher: RecordingDispatcher) -> Iterator[TestClient]:
    app = create_app(integration_settings(), dispatcher=dispatcher)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


@pytest.fixture
def users(api: TestClient) -> dict[str, dict[str, str]]:
    create_user("ed@example.com", Role.EDITOR)
    create_user("op@example.com", Role.OPERATOR)
    create_user("vi@example.com", Role.VIEWER)
    return {
        "editor": login_headers(api, "ed@example.com"),
        "operator": login_headers(api, "op@example.com"),
        "viewer": login_headers(api, "vi@example.com"),
    }


def _published(api: TestClient, editor: dict[str, str], *, publish: bool = True) -> str:
    created = api.post(
        "/api/v1/workflows", json={"name": "Totales", "definition": DEFINITION}, headers=editor
    )
    wid: str = created.json()["id"]
    if publish:
        api.post(f"/api/v1/workflows/{wid}/publish", json={"expected_revision": 1}, headers=editor)
    return wid


def test_operator_runs_and_viewer_reads(
    api: TestClient, users: dict[str, dict[str, str]], dispatcher: RecordingDispatcher
) -> None:
    wid = _published(api, users["editor"])
    response = api.post(
        f"/api/v1/workflows/{wid}/runs", json={"input": {"amount": 9.5}}, headers=users["operator"]
    )
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["status"] == "pending"
    assert run["version"] == 1
    assert [s["step_id"] for s in run["steps"]] == ["calc"]
    assert len(dispatcher.items) == 1  # encolado tras el commit

    assert api.get(f"/api/v1/runs/{run['id']}", headers=users["viewer"]).status_code == 200
    listing = api.get("/api/v1/runs", params={"workflow_id": wid}, headers=users["viewer"]).json()
    assert listing["total"] == 1
    denied = api.post(
        f"/api/v1/workflows/{wid}/runs", json={"input": {"amount": 1}}, headers=users["viewer"]
    )
    assert denied.status_code == 403


def test_idempotent_creation_returns_same_run(
    api: TestClient, users: dict[str, dict[str, str]], dispatcher: RecordingDispatcher
) -> None:
    wid = _published(api, users["editor"])
    body = {"input": {"amount": 1}, "idempotency_key": "pedido-77"}
    first = api.post(f"/api/v1/workflows/{wid}/runs", json=body, headers=users["operator"])
    second = api.post(f"/api/v1/workflows/{wid}/runs", json=body, headers=users["operator"])
    assert (first.status_code, second.status_code) == (202, 200)
    assert first.json()["id"] == second.json()["id"]
    assert len(dispatcher.items) == 1  # la repetición no vuelve a encolar

    changed = api.post(
        f"/api/v1/workflows/{wid}/runs",
        json={"input": {"amount": 2}, "idempotency_key": "pedido-77"},
        headers=users["operator"],
    )
    assert changed.status_code == 409


def test_input_validated_against_declared_inputs(
    api: TestClient, users: dict[str, dict[str, str]]
) -> None:
    wid = _published(api, users["editor"])
    response = api.post(
        f"/api/v1/workflows/{wid}/runs",
        json={"input": {"amount": "mucho", "otro": 1}},
        headers=users["operator"],
    )
    assert response.status_code == 422
    fields = {e["field"] for e in response.json()["errors"]}
    assert fields == {"amount", "otro"}


def test_unpublished_or_archived_workflow_cannot_run(
    api: TestClient, users: dict[str, dict[str, str]]
) -> None:
    wid = _published(api, users["editor"], publish=False)
    url = f"/api/v1/workflows/{wid}/runs"
    assert (
        api.post(url, json={"input": {"amount": 1}}, headers=users["operator"]).status_code == 409
    )
    api.post(
        f"/api/v1/workflows/{wid}/publish", json={"expected_revision": 1}, headers=users["editor"]
    )
    api.patch(f"/api/v1/workflows/{wid}", json={"status": "archived"}, headers=users["editor"])
    assert (
        api.post(url, json={"input": {"amount": 1}}, headers=users["operator"]).status_code == 409
    )


def test_cancel_and_invalid_resolve(api: TestClient, users: dict[str, dict[str, str]]) -> None:
    wid = _published(api, users["editor"])
    run_id = api.post(
        f"/api/v1/workflows/{wid}/runs", json={"input": {"amount": 1}}, headers=users["operator"]
    ).json()["id"]

    resolve = api.post(
        f"/api/v1/runs/{run_id}/steps/calc/resolve",
        json={"action": "retry"},
        headers=users["operator"],
    )
    assert resolve.status_code == 409  # el paso no está en revisión

    cancelled = api.post(f"/api/v1/runs/{run_id}/cancel", headers=users["operator"])
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["steps"][0]["status"] == "cancelled"
    assert api.post(f"/api/v1/runs/{run_id}/cancel", headers=users["operator"]).status_code == 409

    bad_step = api.post(
        f"/api/v1/runs/{run_id}/steps/NO VALE/resolve",
        json={"action": "retry"},
        headers=users["operator"],
    )
    assert bad_step.status_code == 422


def test_run_filters(api: TestClient, users: dict[str, dict[str, str]]) -> None:
    wid = _published(api, users["editor"])
    for amount in (1, 2):
        api.post(
            f"/api/v1/workflows/{wid}/runs",
            json={"input": {"amount": amount}},
            headers=users["operator"],
        )
    pending = api.get("/api/v1/runs", params={"status": "pending"}, headers=users["viewer"]).json()
    assert pending["total"] == 2
    done = api.get("/api/v1/runs", params={"status": "succeeded"}, headers=users["viewer"]).json()
    assert done["total"] == 0


def test_run_creation_is_rate_limited_per_user(dispatcher: RecordingDispatcher) -> None:
    app = create_app(integration_settings(run_create_rate_limit_per_user=3), dispatcher=dispatcher)
    with TestClient(app, base_url="https://testserver") as api:
        create_user("ed@example.com", Role.EDITOR)
        create_user("op1@example.com", Role.OPERATOR)
        create_user("op2@example.com", Role.OPERATOR)
        editor = login_headers(api, "ed@example.com")
        workflow_id = _published(api, editor)
        op1 = login_headers(api, "op1@example.com")
        url = f"/api/v1/workflows/{workflow_id}/runs"
        codes = [
            api.post(url, json={"input": {"amount": i}}, headers=op1).status_code for i in range(4)
        ]
        assert codes == [202, 202, 202, 429]
        # El límite es por usuario: otro operador no se ve afectado.
        op2 = login_headers(api, "op2@example.com")
        assert api.post(url, json={"input": {"amount": 1}}, headers=op2).status_code == 202
