"""Esquemas de la API de credenciales. El secreto entra, pero nunca sale."""

from __future__ import annotations

import ipaddress
import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from nexusflow.integrations.adapters import CredentialType

CREDENTIAL_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,99}$"
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"
)


def _host(value: str) -> str:
    host = value.strip().lower()
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if not _HOSTNAME.fullmatch(host):
        raise ValueError(f"'{value}' no es un nombre de host válido (sin esquema ni ruta)")
    return host


Host = Annotated[str, AfterValidator(_host)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CredentialCreate(_Strict):
    name: str = Field(pattern=CREDENTIAL_NAME_PATTERN)
    type: CredentialType
    description: str | None = Field(default=None, max_length=500)
    allowed_hosts: list[Host] = Field(min_length=1, max_length=20)
    secret: dict[str, Any] = Field(
        description="Según el tipo: {token} | {header_name, value} | {username, password}"
    )


class CredentialUpdate(_Strict):
    description: str | None = Field(default=None, max_length=500)
    allowed_hosts: list[Host] | None = Field(default=None, min_length=1, max_length=20)


class CredentialSecretUpdate(_Strict):
    secret: dict[str, Any]


class CredentialView(BaseModel):
    """Metadatos de una credencial. Deliberadamente no tiene ningún campo de secreto."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: CredentialType
    description: str | None
    allowed_hosts: list[str]
    hint: str
    key_fingerprint: str
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    secret_updated_at: datetime | None
    last_used_at: datetime | None
