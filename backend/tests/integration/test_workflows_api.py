"""API de flujos: CRUD, borradores, publicación, inmutabilidad y concurrencia."""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nexusflow.audit.service import RequestContext
from nexusflow.auth.models import User
from nexusflow.auth.permissions import Role
from nexusflow.core.errors import ConflictError
from nexusflow.workflows import service
from nexusflow.workflows.dsl import WorkflowDefinition
from tests.integration.conftest import create_user, db_url, fetch_all, login_headers, run_db

pytestmark = pytest.mark.integration

API = "/api/v1/workflows"

DEFINITION: dict[str, Any] = {
    "inputs": [{"name": "customer_id", "type": "string"}],
    "start": "lookup",
    "steps": [
        {
            "id": "lookup",
            "type": "http_request",
            "config": {
                "method": "GET",
                "url": "https://crm.example.com/customers/{{ input.customer_id }}",
            },
            "next": "is_vip",
        },
        {
            "id": "is_vip",
            "type": "condition",
            "config": {
                "when": {
                    "not": {"field": "steps.lookup.output.body.tier", "op": "ne", "value": "vip"}
                }
            },
            "on_true": "tag",
        },
        {
            "id": "tag",
            "type": "transform",
            "config": {"output": {"customer": "{{ steps.lookup.output.body.id }}"}},
        },
    ],
}


def _broken() -> dict[str, Any]:
    data = copy.deepcopy(DEFINITION)
    data["steps"][0]["next"] = "ghost"
    return data


@pytest.fixture
def editor(client: TestClient) -> dict[str, str]:
    create_user("editor@example.com", Role.EDITOR)
    return login_headers(client, "editor@example.com")


def _create(client: TestClient, headers: dict[str, str], name: str = "Alta VIP") -> dict[str, Any]:
    response = client.post(
        API,
        json={"name": name, "description": "Etiqueta clientes VIP", "definition": DEFINITION},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# ------------------------------------------------------------------ creación y lectura


def test_create_returns_valid_draft(client: TestClient, editor: dict[str, str]) -> None:
    body = _create(client, editor)
    assert body["status"] == "active"
    assert body["draft_version"] == 1
    assert body["published_version"] is None
    draft = body["draft"]
    assert draft["state"] == "draft"
    assert draft["revision"] == 1
    assert draft["issues"] == []
    # La condición "not" se guarda y se devuelve con su nombre público.
    assert draft["definition"]["steps"][1]["config"]["when"]["not"]["op"] == "ne"
    assert len(draft["definition_hash"]) == 64


def test_viewer_reads_but_cannot_write(client: TestClient, editor: dict[str, str]) -> None:
    created = _create(client, editor)
    create_user("viewer@example.com", Role.VIEWER)
    viewer = login_headers(client, "viewer@example.com")
    assert client.get(f"{API}/{created['id']}", headers=viewer).status_code == 200
    assert client.get(API, headers=viewer).status_code == 200
    denied = client.post(API, json={"name": "x", "definition": DEFINITION}, headers=viewer)
    assert denied.status_code == 403
    put = client.put(
        f"{API}/{created['id']}/draft",
        json={"definition": DEFINITION, "expected_revision": 1},
        headers=viewer,
    )
    assert put.status_code == 403


def test_operator_cannot_write(client: TestClient) -> None:
    create_user("op@example.com", Role.OPERATOR)
    headers = login_headers(client, "op@example.com")
    assert (
        client.post(API, json={"name": "x", "definition": DEFINITION}, headers=headers).status_code
        == 403
    )


def test_duplicate_name_conflict(client: TestClient, editor: dict[str, str]) -> None:
    _create(client, editor, "Único")
    response = client.post(API, json={"name": "Único", "definition": DEFINITION}, headers=editor)
    assert response.status_code == 409


def test_structurally_invalid_definition_rejected(
    client: TestClient, editor: dict[str, str]
) -> None:
    data = copy.deepcopy(DEFINITION)
    data["steps"][0]["type"] = "python_script"
    response = client.post(API, json={"name": "x", "definition": data}, headers=editor)
    assert response.status_code == 422


def test_unknown_workflow_is_404(client: TestClient, editor: dict[str, str]) -> None:
    assert client.get(f"{API}/{uuid.uuid4()}", headers=editor).status_code == 404
    assert client.get(f"{API}/no-es-uuid", headers=editor).status_code == 422


# ------------------------------------------------------------------ borradores y publicación


def test_full_lifecycle(client: TestClient, editor: dict[str, str]) -> None:
    wid = _create(client, editor)["id"]

    # Un borrador con incidencias se puede guardar...
    saved = client.put(
        f"{API}/{wid}/draft", json={"definition": _broken(), "expected_revision": 1}, headers=editor
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 2
    # Romper el enlace deja además inalcanzables los pasos siguientes.
    codes = [i["code"] for i in saved.json()["issues"]]
    assert codes == ["unknown_step", "unreachable_step", "unreachable_step"]

    # ...pero no publicar.
    refused = client.post(f"{API}/{wid}/publish", json={"expected_revision": 2}, headers=editor)
    assert refused.status_code == 422
    assert refused.json()["issues"][0]["code"] == "unknown_step"

    fixed = client.put(
        f"{API}/{wid}/draft",
        json={"definition": DEFINITION, "expected_revision": 2},
        headers=editor,
    )
    assert fixed.json()["revision"] == 3

    published = client.post(f"{API}/{wid}/publish", json={"expected_revision": 3}, headers=editor)
    assert published.status_code == 200
    assert published.json()["state"] == "published"
    assert published.json()["version"] == 1

    detail = client.get(f"{API}/{wid}", headers=editor).json()
    assert detail["published_version"] == 1
    assert detail["draft"] is None

    # Editar tras publicar abre la versión 2 sin tocar la 1.
    v2 = client.put(f"{API}/{wid}/draft", json={"definition": DEFINITION}, headers=editor)
    assert v2.status_code == 200
    assert (v2.json()["version"], v2.json()["revision"]) == (2, 1)

    versions = client.get(f"{API}/{wid}/versions", headers=editor).json()
    assert [(v["version"], v["state"]) for v in versions] == [(2, "draft"), (1, "published")]
    assert client.get(f"{API}/{wid}/versions/1", headers=editor).json()["state"] == "published"
    assert client.get(f"{API}/{wid}/versions/9", headers=editor).status_code == 404

    actions = [r["action"] for r in fetch_all("SELECT action FROM audit_log ORDER BY id")]
    assert actions.count("workflow.draft_saved") == 3
    assert "workflow.published" in actions


def test_optimistic_concurrency_on_draft(client: TestClient, editor: dict[str, str]) -> None:
    wid = _create(client, editor)["id"]
    url = f"{API}/{wid}/draft"
    missing = client.put(url, json={"definition": DEFINITION}, headers=editor)
    assert missing.status_code == 409  # hay borrador: expected_revision es obligatorio
    assert (
        client.put(
            url, json={"definition": DEFINITION, "expected_revision": 1}, headers=editor
        ).status_code
        == 200
    )
    stale = client.put(url, json={"definition": DEFINITION, "expected_revision": 1}, headers=editor)
    assert stale.status_code == 409
    stale_publish = client.post(
        f"{API}/{wid}/publish", json={"expected_revision": 1}, headers=editor
    )
    assert stale_publish.status_code == 409


def test_publish_without_draft_conflicts(client: TestClient, editor: dict[str, str]) -> None:
    wid = _create(client, editor)["id"]
    assert (
        client.post(
            f"{API}/{wid}/publish", json={"expected_revision": 1}, headers=editor
        ).status_code
        == 200
    )
    again = client.post(f"{API}/{wid}/publish", json={"expected_revision": 1}, headers=editor)
    assert again.status_code == 409


def test_published_version_is_immutable_in_database(
    client: TestClient, editor: dict[str, str]
) -> None:
    wid = _create(client, editor)["id"]
    client.post(f"{API}/{wid}/publish", json={"expected_revision": 1}, headers=editor)

    async def tamper(session: AsyncSession) -> None:
        await session.execute(
            text("UPDATE workflow_versions SET definition = '{}'::jsonb WHERE workflow_id = :w"),
            {"w": wid},
        )

    async def delete(session: AsyncSession) -> None:
        await session.execute(
            text("DELETE FROM workflow_versions WHERE workflow_id = :w"), {"w": wid}
        )

    for operation in (tamper, delete):
        with pytest.raises(DBAPIError, match="inmutable"):
            run_db(operation)


# ------------------------------------------------------------------ archivo, búsqueda, validación


def test_archive_blocks_edits_until_restored(client: TestClient, editor: dict[str, str]) -> None:
    wid = _create(client, editor)["id"]
    archived = client.patch(f"{API}/{wid}", json={"status": "archived"}, headers=editor)
    assert archived.json()["status"] == "archived"
    blocked = client.put(
        f"{API}/{wid}/draft",
        json={"definition": DEFINITION, "expected_revision": 1},
        headers=editor,
    )
    assert blocked.status_code == 409
    client.patch(f"{API}/{wid}", json={"status": "active"}, headers=editor)
    ok = client.put(
        f"{API}/{wid}/draft",
        json={"definition": DEFINITION, "expected_revision": 1},
        headers=editor,
    )
    assert ok.status_code == 200
    actions = [r["action"] for r in fetch_all("SELECT action FROM audit_log ORDER BY id")]
    assert {"workflow.archived", "workflow.restored"} <= set(actions)


def test_list_filters_and_literal_search(client: TestClient, editor: dict[str, str]) -> None:
    _create(client, editor, "Facturas 100%")
    _create(client, editor, "Facturas 1000")
    other = _create(client, editor, "Onboarding")
    client.patch(f"{API}/{other['id']}", json={"status": "archived"}, headers=editor)

    by_text = client.get(API, params={"q": "100%"}, headers=editor).json()
    assert [w["name"] for w in by_text["items"]] == ["Facturas 100%"]  # '%' no es comodín

    active = client.get(API, params={"status": "active"}, headers=editor).json()
    assert active["total"] == 2
    page = client.get(API, params={"limit": 1}, headers=editor).json()
    assert (page["total"], len(page["items"])) == (3, 1)


def test_validate_endpoint(client: TestClient, editor: dict[str, str]) -> None:
    ok = client.post(f"{API}/validate", json={"definition": DEFINITION}, headers=editor).json()
    assert ok == {"valid": True, "issues": []}
    bad = client.post(f"{API}/validate", json={"definition": _broken()}, headers=editor).json()
    assert bad["valid"] is False


def test_request_body_size_limit(make_client: Callable[..., TestClient]) -> None:
    client = make_client(max_request_body_bytes=2048)
    create_user("big@example.com", Role.EDITOR)
    headers = login_headers(client, "big@example.com")
    data = copy.deepcopy(DEFINITION)
    data["steps"][2]["config"]["output"]["padding"] = "x" * 5000
    response = client.post(API, json={"name": "grande", "definition": data}, headers=headers)
    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/problem+json")


# ------------------------------------------------------------------ concurrencia real


def test_concurrent_draft_saves_with_same_revision() -> None:
    owner = create_user("race@example.com", Role.EDITOR)
    definition = WorkflowDefinition.model_validate(DEFINITION)
    ctx = RequestContext(request_id="race")

    async def scenario() -> list[object]:
        engine = create_async_engine(db_url(), pool_size=5)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as s:
                actor = await s.get(User, owner.id)
                assert actor is not None
                from nexusflow.workflows.schemas import WorkflowCreate

                state = await service.create_workflow(
                    s, actor, WorkflowCreate(name="Carrera", definition=definition), ctx
                )
                wid = state.workflow.id

            async def save() -> object:
                async with factory() as s:
                    actor = await s.get(User, owner.id)
                    assert actor is not None
                    return await service.save_draft(s, actor, wid, definition, 1, ctx)

            return await asyncio.gather(*(save() for _ in range(4)), return_exceptions=True)
        finally:
            await engine.dispose()

    results = asyncio.run(scenario())
    assert sum(isinstance(r, ConflictError) for r in results) == 3, results
    assert sum(not isinstance(r, Exception) for r in results) == 1
