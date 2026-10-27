"""Pruebas de la app sin dependencias externas: middleware, errores y liveness."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from nexusflow.core.errors import PROBLEM_JSON, NotFoundError
from nexusflow.core.middleware import SECURITY_HEADERS, resolve_request_id
from nexusflow.main import create_app
from tests.conftest import make_settings


def _app_with_test_routes() -> FastAPI:
    app = create_app(make_settings())

    @app.get("/_test/not-found")
    async def raise_not_found() -> None:
        raise NotFoundError("El flujo 42 no existe")

    @app.get("/_test/boom")
    async def raise_unexpected() -> None:
        raise RuntimeError("detalle interno con password=supersecreto")

    @app.get("/_test/validate")
    async def validate(limit: int = Query(ge=1, le=100)) -> dict[str, int]:
        return {"limit": limit}

    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(_app_with_test_routes(), raise_server_exceptions=False) as test_client:
        yield test_client


def test_live_ok(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_request_id_generated_and_security_headers(client: TestClient) -> None:
    response = client.get("/health/live")
    assert len(response.headers["X-Request-ID"]) == 32
    for header, value in SECURITY_HEADERS.items():
        assert response.headers[header] == value


def test_safe_incoming_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "abc-123.def_4"})
    assert response.headers["X-Request-ID"] == "abc-123.def_4"


@pytest.mark.parametrize("value", ["a b", "x" * 65, "<script>", "id\r\nX-Evil: 1", ""])
def test_unsafe_request_id_replaced(value: str) -> None:
    resolved = resolve_request_id(value)
    assert resolved != value
    assert len(resolved) == 32


def test_unknown_route_returns_problem_json(client: TestClient) -> None:
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["status"] == 404
    assert body["title"] == "Not Found"
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_domain_error_maps_to_problem(client: TestClient) -> None:
    body = client.get("/_test/not-found").json()
    assert body["status"] == 404
    assert body["type"].endswith("/not-found")
    assert body["detail"] == "El flujo 42 no existe"
    assert body["instance"] == "/_test/not-found"


def test_validation_error_does_not_echo_input(client: TestClient) -> None:
    response = client.get("/_test/validate", params={"limit": "999"})
    assert response.status_code == 422
    body = response.json()
    assert body["type"].endswith("/validation-error")
    assert body["errors"][0]["loc"] == ["query", "limit"]
    assert "input" not in body["errors"][0]
    assert "999" not in response.text


def test_unhandled_error_hides_internals(client: TestClient) -> None:
    response = client.get("/_test/boom")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert "supersecreto" not in response.text
    assert "RuntimeError" not in response.text
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_method_not_allowed_is_problem(client: TestClient) -> None:
    response = client.post("/health/live")
    assert response.status_code == 405
    assert response.json()["status"] == 405


def test_docs_can_be_disabled() -> None:
    app = create_app(make_settings(docs_enabled=False))
    with TestClient(app) as test_client:
        assert test_client.get("/docs").status_code == 404
        assert test_client.get("/openapi.json").status_code == 404


def test_cors_only_allows_configured_origin() -> None:
    app = create_app(make_settings(cors_origins=["http://localhost:5173"]))
    with TestClient(app) as test_client:
        allowed = test_client.get("/health/live", headers={"Origin": "http://localhost:5173"})
        denied = test_client.get("/health/live", headers={"Origin": "http://evil.example"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in denied.headers


def test_readiness_reports_unavailable_dependencies(client: TestClient) -> None:
    # Las URLs de prueba apuntan a un puerto cerrado: la app debe responder 503, no colgarse.
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["checks"]["database"]["status"] == "error"
    assert body["checks"]["redis"]["status"] == "error"
