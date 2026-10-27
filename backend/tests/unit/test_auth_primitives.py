"""Primitivas de seguridad: hash de contraseñas, JWT, permisos y validación de entrada."""

from __future__ import annotations

import uuid
from datetime import timedelta

import jwt
import pytest
from pydantic import ValidationError

from nexusflow.auth.passwords import hash_password, needs_rehash, verify_password
from nexusflow.auth.permissions import ROLE_PERMISSIONS, Permission, Role, has_permission
from nexusflow.auth.schemas import LoginRequest, RegisterRequest, UserUpdateRequest
from nexusflow.auth.tokens import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from nexusflow.core.errors import UnauthorizedError
from nexusflow.db.types import utcnow
from tests.conftest import make_settings

GOOD_PASSWORD = "correcto-caballo-bateria"

# ------------------------------------------------------------------ contraseñas


def test_password_hash_roundtrip() -> None:
    hashed = hash_password(GOOD_PASSWORD)
    assert hashed.startswith("$argon2id$")
    assert GOOD_PASSWORD not in hashed
    assert verify_password(hashed, GOOD_PASSWORD)
    assert not verify_password(hashed, GOOD_PASSWORD + "x")
    assert not needs_rehash(hashed)


def test_same_password_produces_different_hashes() -> None:
    assert hash_password(GOOD_PASSWORD) != hash_password(GOOD_PASSWORD)  # sal aleatoria


def test_verify_against_garbage_hash_is_false_not_error() -> None:
    assert not verify_password("no-es-un-hash", GOOD_PASSWORD)


# ------------------------------------------------------------------ JWT


def test_access_token_roundtrip() -> None:
    settings = make_settings()
    user_id = uuid.uuid4()
    token = create_access_token(user_id, settings)
    assert decode_access_token(token.token, settings) == user_id


def test_expired_access_token_rejected() -> None:
    settings = make_settings()
    token = create_access_token(uuid.uuid4(), settings, now=utcnow() - timedelta(hours=1))
    with pytest.raises(UnauthorizedError, match="expirado"):
        decode_access_token(token.token, settings)


def test_token_signed_with_other_key_rejected() -> None:
    token = create_access_token(uuid.uuid4(), make_settings(secret_key="otra" * 10))
    with pytest.raises(UnauthorizedError):
        decode_access_token(token.token, make_settings())


@pytest.mark.parametrize(("field", "value"), [("jwt_audience", "otra-api"), ("jwt_issuer", "otro")])
def test_wrong_audience_or_issuer_rejected(field: str, value: str) -> None:
    token = create_access_token(uuid.uuid4(), make_settings(**{field: value}))
    with pytest.raises(UnauthorizedError):
        decode_access_token(token.token, make_settings())


def test_alg_none_rejected() -> None:
    settings = make_settings()
    now = utcnow()
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "type": "access",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=5),
        },
        key=None,
        algorithm="none",
    )
    with pytest.raises(UnauthorizedError):
        decode_access_token(forged, settings)


def test_token_of_other_type_rejected() -> None:
    settings = make_settings()
    now = utcnow()
    other = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "type": "refresh",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=5),
        },
        settings.secret_key.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(UnauthorizedError):
        decode_access_token(other, settings)


def test_refresh_tokens_are_random_and_hashed() -> None:
    first, second = generate_refresh_token(), generate_refresh_token()
    assert first != second
    assert len(first) >= 43  # 32 bytes en base64url
    assert hash_refresh_token(first) != first
    assert len(hash_refresh_token(first)) == 64


# ------------------------------------------------------------------ permisos


def test_admin_has_every_permission() -> None:
    assert ROLE_PERMISSIONS[Role.ADMIN] == frozenset(Permission)


def test_roles_are_strictly_nested() -> None:
    viewer, operator, editor, admin = (
        ROLE_PERMISSIONS[r] for r in (Role.VIEWER, Role.OPERATOR, Role.EDITOR, Role.ADMIN)
    )
    assert viewer < operator < editor < admin


@pytest.mark.parametrize("role", [Role.VIEWER, Role.OPERATOR, Role.EDITOR])
def test_only_admin_manages_users(role: Role) -> None:
    assert not has_permission(role, Permission.USERS_MANAGE)
    assert not has_permission(role, Permission.USERS_READ)


def test_operator_executes_but_does_not_edit() -> None:
    assert has_permission(Role.OPERATOR, Permission.WORKFLOWS_EXECUTE)
    assert not has_permission(Role.OPERATOR, Permission.WORKFLOWS_WRITE)


# ------------------------------------------------------------------ validación de entrada


def test_email_is_normalized() -> None:
    data = RegisterRequest.model_validate(
        {"email": "  Ana.Perez@Example.COM ", "password": GOOD_PASSWORD}
    )
    assert data.email == "ana.perez@example.com"


def test_registration_cannot_set_role() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        RegisterRequest.model_validate(
            {"email": "a@example.com", "password": GOOD_PASSWORD, "role": "admin"}
        )


@pytest.mark.parametrize(
    "password",
    [
        "corta",  # menos de 12
        "x" * 129,  # más de 128
        "aaaaaaaaaaaaaaaa",  # repetitiva
        " espacio-al-inicio",
        "mi-ana.perez-segura",  # contiene el usuario del email
    ],
)
def test_weak_passwords_rejected(password: str) -> None:
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate({"email": "ana.perez@example.com", "password": password})


def test_password_not_exposed_in_model_repr() -> None:
    data = LoginRequest.model_validate({"email": "a@example.com", "password": GOOD_PASSWORD})
    assert GOOD_PASSWORD not in repr(data)
    assert GOOD_PASSWORD not in str(data.model_dump())


def test_user_update_requires_a_field() -> None:
    with pytest.raises(ValidationError, match="al menos un campo"):
        UserUpdateRequest.model_validate({})
