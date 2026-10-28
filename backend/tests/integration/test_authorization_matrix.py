"""Revisión de seguridad automatizada: autorización en TODAS las rutas.

Recorre el contrato OpenAPI y llama a cada operación:
1. Sin credenciales → debe responder 401, salvo las rutas públicas declaradas aquí.
2. Como `viewer` → las operaciones de escritura deben responder 403.

Si alguien añade un endpoint y olvida la autorización, esta prueba falla. Es más fiable que
revisar decoradores a mano (OWASP API5: Broken Function Level Authorization).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from nexusflow.auth.permissions import Role
from nexusflow.engine.dispatch import RecordingDispatcher
from nexusflow.main import create_app
from tests.integration.conftest import create_user, integration_settings, login_headers

pytestmark = pytest.mark.integration

# Rutas sin JWT, cada una con su propio mecanismo de protección.
PUBLIC: dict[tuple[str, str], str] = {
    ("GET", "/health/live"): "health check",
    ("GET", "/health/ready"): "health check",
    ("POST", "/api/v1/auth/register"): "límite por IP; crea solo viewers",
    ("POST", "/api/v1/auth/login"): "límite por IP y por cuenta",
    ("POST", "/api/v1/auth/refresh"): "cookie HttpOnly",
    ("POST", "/api/v1/auth/logout"): "idempotente",
    ("POST", "/api/v1/hooks/{trigger_id}"): "firma HMAC",
    ("GET", "/metrics"): "token Bearer propio",
}

# Lecturas que un viewer SÍ puede hacer (el resto de operaciones de escritura, no).
VIEWER_ALLOWED_WRITES = {("POST", "/api/v1/workflows/validate")}


def _operations(client: TestClient) -> list[tuple[str, str]]:
    spec = client.get("/openapi.json").json()
    return [
        (method.upper(), path)
        for path, item in spec["paths"].items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    ]


def _concrete(path: str) -> str:
    # Parámetros de ruta con valores válidos (UUID o número) que no existen.
    def value(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "version":
            return "1"
        if name == "step_id":
            return "paso"
        if name == "delivery_id" or name.endswith("_id"):
            return str(uuid.uuid4())
        return "x"

    return re.sub(r"\{([^}]+)\}", value, path)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(integration_settings(metrics_token="m" * 30), dispatcher=RecordingDispatcher())
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def test_public_routes_are_exactly_the_declared_ones(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    unauthenticated = {
        (method.upper(), path)
        for path, item in spec["paths"].items()
        for method, op in item.items()
        if method in {"get", "post", "put", "patch", "delete"} and not op.get("security")
    }
    # /metrics usa su propio token y no el esquema HTTPBearer del resto.
    assert unauthenticated == set(PUBLIC), unauthenticated ^ set(PUBLIC)


def test_every_private_operation_requires_authentication(client: TestClient) -> None:
    operations = _operations(client)
    assert len(operations) >= 45  # protege contra una prueba que no recorre nada
    failures = []
    for method, path in operations:
        if (method, path) in PUBLIC:
            continue
        response = client.request(method, _concrete(path), json={})
        if response.status_code != 401:
            failures.append(f"{method} {path} → {response.status_code}")
    assert failures == [], "Rutas privadas accesibles sin token:\n" + "\n".join(failures)


def test_viewer_cannot_perform_any_write(client: TestClient) -> None:
    create_user("lector@example.com", Role.VIEWER)
    headers = login_headers(client, "lector@example.com")
    failures = []
    for method, path in _operations(client):
        if method == "GET" or (method, path) in PUBLIC or (method, path) in VIEWER_ALLOWED_WRITES:
            continue
        response = client.request(method, _concrete(path), json={}, headers=headers)
        # 403 es lo esperado. 422 indicaría que el cuerpo se valida ANTES que el permiso.
        if response.status_code != 403:
            failures.append(f"{method} {path} → {response.status_code}")
    assert failures == [], "Escrituras permitidas a un viewer:\n" + "\n".join(failures)


def test_viewer_cannot_read_admin_resources(client: TestClient) -> None:
    create_user("lector2@example.com", Role.VIEWER)
    headers = login_headers(client, "lector2@example.com")
    for path in (
        "/api/v1/users",
        "/api/v1/audit-logs",
        "/api/v1/credentials",
        "/api/v1/webhook-subscriptions",
    ):
        assert client.get(path, headers=headers).status_code == 403, path
