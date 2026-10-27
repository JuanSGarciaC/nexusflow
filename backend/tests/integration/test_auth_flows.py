"""Flujos de autenticación de punta a punta contra PostgreSQL y Redis reales."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from nexusflow.auth.permissions import Role
from nexusflow.auth.router import REFRESH_COOKIE
from nexusflow.auth.tokens import create_access_token
from nexusflow.db.types import utcnow
from tests.integration.conftest import (
    STRONG_PASSWORD,
    create_user,
    fetch_all,
    integration_settings,
)

pytestmark = pytest.mark.integration

AUTH = "/api/v1/auth"


def _register(client: TestClient, email: str, password: str = STRONG_PASSWORD) -> dict[str, object]:
    response = client.post(f"{AUTH}/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def _login(client: TestClient, email: str, password: str = STRONG_PASSWORD) -> str:
    response = client.post(f"{AUTH}/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


def _refresh_with(client: TestClient, token: str | None) -> int:
    """Llama a /refresh presentando exactamente este refresh token (o ninguno)."""
    client.cookies.clear()
    if token:
        client.cookies.set(REFRESH_COOKIE, token)
    return client.post(f"{AUTH}/refresh").status_code


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------ registro y login


def test_register_login_and_me(client: TestClient) -> None:
    created = _register(client, "Ana@Example.com")
    assert created["email"] == "ana@example.com"
    assert created["role"] == "viewer"
    assert "password_hash" not in created

    login = client.post(
        f"{AUTH}/login", json={"email": "ANA@example.com", "password": STRONG_PASSWORD}
    )
    assert login.status_code == 200
    body = login.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60

    cookie = login.headers["set-cookie"]
    assert f"{REFRESH_COOKIE}=" in cookie
    for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/api/v1/auth"):
        assert attribute.lower() in cookie.lower()

    me = client.get("/api/v1/users/me", headers=_bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "ana@example.com"
    assert "workflows:read" in me.json()["permissions"]
    assert "users:manage" not in me.json()["permissions"]


def test_password_is_stored_hashed(client: TestClient) -> None:
    _register(client, "hash@example.com")
    rows = fetch_all("SELECT password_hash FROM users WHERE email = 'hash@example.com'")
    assert rows[0]["password_hash"].startswith("$argon2id$")
    assert STRONG_PASSWORD not in rows[0]["password_hash"]


def test_duplicate_email_conflict_is_case_insensitive(client: TestClient) -> None:
    _register(client, "dup@example.com")
    response = client.post(
        f"{AUTH}/register", json={"email": "DUP@example.com", "password": STRONG_PASSWORD}
    )
    assert response.status_code == 409


def test_registration_can_be_disabled(make_client: Callable[..., TestClient]) -> None:
    client = make_client(registration_enabled=False)
    response = client.post(
        f"{AUTH}/register", json={"email": "x@example.com", "password": STRONG_PASSWORD}
    )
    assert response.status_code == 403


def test_wrong_password_and_unknown_email_look_identical(client: TestClient) -> None:
    _register(client, "real@example.com")
    wrong = client.post(f"{AUTH}/login", json={"email": "real@example.com", "password": "mala"})
    unknown = client.post(f"{AUTH}/login", json={"email": "nadie@example.com", "password": "mala"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]
    assert "set-cookie" not in wrong.headers


def test_inactive_user_cannot_login(client: TestClient) -> None:
    create_user("off@example.com", active=False)
    response = client.post(
        f"{AUTH}/login", json={"email": "off@example.com", "password": STRONG_PASSWORD}
    )
    assert response.status_code == 401


def test_expired_access_token_rejected(client: TestClient) -> None:
    user = create_user("exp@example.com")
    old = create_access_token(user.id, integration_settings(), now=utcnow() - timedelta(hours=1))
    response = client.get("/api/v1/users/me", headers=_bearer(old.token))
    assert response.status_code == 401
    assert "expirado" in response.json()["detail"]


def test_token_for_nonexistent_user_rejected(client: TestClient) -> None:
    token = create_access_token(uuid.uuid4(), integration_settings())
    assert client.get("/api/v1/users/me", headers=_bearer(token.token)).status_code == 401


# ------------------------------------------------------------------ refresh y logout


def test_refresh_rotates_cookie(client: TestClient) -> None:
    _register(client, "rot@example.com")
    _login(client, "rot@example.com")
    first = client.cookies.get(REFRESH_COOKIE)

    response = client.post(f"{AUTH}/refresh")
    assert response.status_code == 200
    second = client.cookies.get(REFRESH_COOKIE)
    assert second
    assert second != first

    me = client.get("/api/v1/users/me", headers=_bearer(response.json()["access_token"]))
    assert me.status_code == 200


def test_refresh_token_reuse_revokes_whole_family(client: TestClient) -> None:
    _register(client, "reuse@example.com")
    _login(client, "reuse@example.com")
    stolen = client.cookies.get(REFRESH_COOKIE)
    assert stolen

    # El usuario legítimo rota su token...
    assert client.post(f"{AUTH}/refresh").status_code == 200
    legit = client.cookies.get(REFRESH_COOKIE)

    # ...y el atacante usa la copia antigua: se detecta la reutilización.
    assert _refresh_with(client, stolen) == 401

    # Toda la familia queda revocada: el token legítimo tampoco sirve ya.
    assert _refresh_with(client, legit) == 401

    actions = [r["action"] for r in fetch_all("SELECT action FROM audit_log ORDER BY id")]
    assert "auth.refresh.reuse_detected" in actions


def test_refresh_with_unknown_or_missing_cookie(client: TestClient) -> None:
    assert _refresh_with(client, None) == 401
    assert _refresh_with(client, "inventado") == 401


def test_logout_revokes_session(client: TestClient) -> None:
    _register(client, "bye@example.com")
    _login(client, "bye@example.com")
    token = client.cookies.get(REFRESH_COOKIE)

    response = client.post(f"{AUTH}/logout")
    assert response.status_code == 204
    assert REFRESH_COOKIE in response.headers["set-cookie"]  # se borra la cookie

    assert _refresh_with(client, token) == 401


# ------------------------------------------------------------------ autorización (RBAC)


def test_viewer_cannot_list_or_manage_users(client: TestClient) -> None:
    _register(client, "viewer@example.com")
    token = _login(client, "viewer@example.com")
    assert client.get("/api/v1/users", headers=_bearer(token)).status_code == 403
    target = create_user("target@example.com")
    patch = client.patch(
        f"/api/v1/users/{target.id}", json={"role": "admin"}, headers=_bearer(token)
    )
    assert patch.status_code == 403


def test_admin_lists_users_with_pagination(client: TestClient) -> None:
    create_user("admin@example.com", Role.ADMIN)
    for i in range(3):
        create_user(f"u{i}@example.com")
    token = _login(client, "admin@example.com")

    page = client.get("/api/v1/users", params={"limit": 2}, headers=_bearer(token))
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 4
    assert len(body["items"]) == 2

    too_big = client.get("/api/v1/users", params={"limit": 1000}, headers=_bearer(token))
    assert too_big.status_code == 422


def test_role_change_takes_effect_immediately_and_is_audited(client: TestClient) -> None:
    admin = create_user("boss@example.com", Role.ADMIN)
    worker = create_user("worker@example.com", Role.ADMIN)
    admin_token = _login(client, "boss@example.com")
    worker_token = _login(client, "worker@example.com")
    assert client.get("/api/v1/users", headers=_bearer(worker_token)).status_code == 200

    demote = client.patch(
        f"/api/v1/users/{worker.id}", json={"role": "viewer"}, headers=_bearer(admin_token)
    )
    assert demote.status_code == 200
    assert demote.json()["role"] == "viewer"

    # El mismo access token (aún vigente) ya no concede permisos de admin.
    assert client.get("/api/v1/users", headers=_bearer(worker_token)).status_code == 403

    audit = fetch_all(
        "SELECT actor_user_id, details FROM audit_log WHERE action = 'user.role_changed'"
    )
    assert audit[0]["actor_user_id"] == admin.id
    assert audit[0]["details"] == {"from": "admin", "to": "viewer"}


def test_deactivation_kills_access_and_refresh(client: TestClient) -> None:
    create_user("chief@example.com", Role.ADMIN)
    target = create_user("leaver@example.com")
    admin_token = _login(client, "chief@example.com")
    leaver_token = _login(client, "leaver@example.com")
    leaver_refresh = client.cookies.get(REFRESH_COOKIE)

    response = client.patch(
        f"/api/v1/users/{target.id}", json={"is_active": False}, headers=_bearer(admin_token)
    )
    assert response.status_code == 200

    assert client.get("/api/v1/users/me", headers=_bearer(leaver_token)).status_code == 401
    assert _refresh_with(client, leaver_refresh) == 401


def test_admin_cannot_modify_self(client: TestClient) -> None:
    admin = create_user("self@example.com", Role.ADMIN)
    token = _login(client, "self@example.com")
    response = client.patch(
        f"/api/v1/users/{admin.id}", json={"is_active": False}, headers=_bearer(token)
    )
    assert response.status_code == 403


def test_patch_unknown_user_and_invalid_role(client: TestClient) -> None:
    create_user("adm@example.com", Role.ADMIN)
    token = _login(client, "adm@example.com")
    missing = client.patch(
        f"/api/v1/users/{uuid.uuid4()}", json={"role": "editor"}, headers=_bearer(token)
    )
    assert missing.status_code == 404
    bad_role = client.patch(
        f"/api/v1/users/{uuid.uuid4()}", json={"role": "superadmin"}, headers=_bearer(token)
    )
    assert bad_role.status_code == 422


# ------------------------------------------------------------------ límites de uso y auditoría


def test_login_rate_limit_per_account(make_client: Callable[..., TestClient]) -> None:
    client = make_client(login_rate_limit_per_email=3)
    create_user("brute@example.com")
    payload = {"email": "brute@example.com", "password": "intento-fallido"}
    statuses = [client.post(f"{AUTH}/login", json=payload).status_code for _ in range(4)]
    assert statuses == [401, 401, 401, 429]

    blocked = client.post(f"{AUTH}/login", json=payload)
    assert int(blocked.headers["Retry-After"]) > 0
    # Bloqueada la cuenta, ni siquiera la contraseña correcta entra hasta que pase la ventana.
    good = client.post(
        f"{AUTH}/login", json={"email": "brute@example.com", "password": STRONG_PASSWORD}
    )
    assert good.status_code == 429


def test_register_rate_limit_per_ip(make_client: Callable[..., TestClient]) -> None:
    client = make_client(register_rate_limit_per_ip=2)
    codes = [
        client.post(
            f"{AUTH}/register", json={"email": f"r{i}@example.com", "password": STRONG_PASSWORD}
        ).status_code
        for i in range(3)
    ]
    assert codes == [201, 201, 429]


def test_failed_login_is_audited_without_password(client: TestClient) -> None:
    create_user("audit@example.com")
    client.post(f"{AUTH}/login", json={"email": "audit@example.com", "password": "secreto-erroneo"})
    rows = fetch_all("SELECT action, details, ip_address::text AS ip, request_id FROM audit_log")
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "auth.login.failed"
    assert row["details"] == {"email": "audit@example.com", "reason": "bad_password"}
    assert "secreto-erroneo" not in str(row)
    assert row["request_id"]


def test_successful_login_updates_last_login(client: TestClient) -> None:
    create_user("last@example.com")
    _login(client, "last@example.com")
    rows = fetch_all("SELECT last_login_at FROM users WHERE email = 'last@example.com'")
    assert rows[0]["last_login_at"] is not None
