"""Adaptadores de autenticación para APIs REST externas (patrón Strategy).

Cada tipo de credencial sabe validar su secreto y convertirlo en cabeceras HTTP. Añadir
un esquema nuevo (p. ej. OAuth2 client credentials) es añadir una clase y registrarla,
sin tocar el motor.
"""

from __future__ import annotations

import base64
from enum import StrEnum
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class CredentialType(StrEnum):
    BEARER_TOKEN = "bearer_token"  # noqa: S105  # nombre de tipo, no un secreto
    API_KEY_HEADER = "api_key_header"
    BASIC_AUTH = "basic_auth"


class _Secret(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BearerSecret(_Secret):
    token: SecretStr = Field(min_length=1, max_length=4096)


class ApiKeySecret(_Secret):
    header_name: str = Field(default="X-API-Key", min_length=1, max_length=100)
    value: SecretStr = Field(min_length=1, max_length=4096)

    @field_validator("header_name")
    @classmethod
    def _header(cls, name: str) -> str:
        if not all(c.isalnum() or c in "-_" for c in name):
            raise ValueError("Nombre de cabecera no válido")
        if name.lower() in {"host", "content-length", "transfer-encoding", "cookie"}:
            raise ValueError(f"La cabecera '{name}' no se puede usar para credenciales")
        return name


class BasicSecret(_Secret):
    username: str = Field(min_length=1, max_length=256)
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator("username")
    @classmethod
    def _no_colon(cls, value: str) -> str:
        if ":" in value:
            raise ValueError("El usuario de Basic Auth no puede contener ':'")
        return value


class AuthAdapter(Protocol):
    secret_model: ClassVar[type[_Secret]]

    def headers(self, secret: dict[str, Any]) -> dict[str, str]: ...

    def secret_values(self, secret: dict[str, Any]) -> list[str]: ...

    def hint(self, secret: dict[str, Any]) -> str: ...


def _tail(value: str) -> str:
    return f"…{value[-4:]}" if len(value) >= 12 else "…"


class BearerAdapter:
    secret_model: ClassVar[type[_Secret]] = BearerSecret

    def headers(self, secret: dict[str, Any]) -> dict[str, str]:
        return {"Authorization": f"Bearer {secret['token']}"}

    def secret_values(self, secret: dict[str, Any]) -> list[str]:
        return [secret["token"]]

    def hint(self, secret: dict[str, Any]) -> str:
        return _tail(secret["token"])


class ApiKeyAdapter:
    secret_model: ClassVar[type[_Secret]] = ApiKeySecret

    def headers(self, secret: dict[str, Any]) -> dict[str, str]:
        return {secret["header_name"]: secret["value"]}

    def secret_values(self, secret: dict[str, Any]) -> list[str]:
        return [secret["value"]]

    def hint(self, secret: dict[str, Any]) -> str:
        return f"{secret['header_name']}: {_tail(secret['value'])}"


class BasicAdapter:
    secret_model: ClassVar[type[_Secret]] = BasicSecret

    def headers(self, secret: dict[str, Any]) -> dict[str, str]:
        raw = f"{secret['username']}:{secret['password']}".encode()
        return {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}

    def secret_values(self, secret: dict[str, Any]) -> list[str]:
        raw = f"{secret['username']}:{secret['password']}".encode()
        return [secret["password"], base64.b64encode(raw).decode()]

    def hint(self, secret: dict[str, Any]) -> str:
        return f"{secret['username']} / …"


ADAPTERS: dict[CredentialType, AuthAdapter] = {
    CredentialType.BEARER_TOKEN: BearerAdapter(),
    CredentialType.API_KEY_HEADER: ApiKeyAdapter(),
    CredentialType.BASIC_AUTH: BasicAdapter(),
}


def parse_secret(credential_type: CredentialType, data: dict[str, Any]) -> dict[str, Any]:
    """Valida el secreto según su tipo y lo devuelve como dict plano para cifrarlo."""
    model = ADAPTERS[credential_type].secret_model.model_validate(data)
    plain: dict[str, Any] = {}
    for key, value in model:
        plain[key] = value.get_secret_value() if isinstance(value, SecretStr) else value
    return plain
