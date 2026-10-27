"""Comportamiento de la API de auth cuando no hay token o los servicios no responden."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from nexusflow.auth.dependencies import client_ip
from nexusflow.main import create_app
from tests.conftest import make_settings


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Las URLs de make_settings apuntan a un puerto cerrado: PostgreSQL y Redis "caídos".
    with TestClient(create_app(make_settings()), base_url="https://testserver") as c:
        yield c


def test_protected_endpoint_without_token_is_401(client: TestClient) -> None:
    response = client.get("/api/v1/users/me")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.headers["content-type"].startswith("application/problem+json")


def test_malformed_token_is_401(client: TestClient) -> None:
    response = client.get("/api/v1/users/me", headers={"Authorization": "Bearer abc.def.ghi"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Token inválido."


def test_basic_auth_scheme_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/users/me", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert response.status_code == 401


def test_login_fails_closed_when_rate_limiter_is_down(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "lo-que-sea"}
    )
    assert response.status_code == 503
    assert response.json()["type"].endswith("/service-unavailable")


def test_logout_without_cookie_is_idempotent(client: TestClient) -> None:
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 204


def test_openapi_documents_bearer_security(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "HTTPBearer" in schema["components"]["securitySchemes"]
    assert "/api/v1/auth/login" in schema["paths"]
    assert "/api/v1/users/{user_id}" in schema["paths"]


@pytest.mark.parametrize(
    ("host", "expected"),
    [("127.0.0.1", "127.0.0.1"), ("::1", "::1"), ("testclient", None), ("", None)],
)
def test_client_ip_only_accepts_real_ips(host: str, expected: str | None) -> None:
    scope: dict[str, object] = {
        "type": "http",
        "client": (host, 1234) if host else None,
        "headers": [],
    }
    assert client_ip(Request(scope)) == expected


def test_validation_messages_have_no_pydantic_prefix(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register", json={"email": "a@example.com", "password": "corta"}
    )
    # FastAPI valida el cuerpo antes de ejecutar el endpoint (y su límite de uso).
    assert response.status_code == 422
    assert response.json()["errors"][0]["msg"].startswith("La contraseña")
