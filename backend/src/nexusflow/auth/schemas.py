"""Esquemas de entrada y salida de la API de autenticación y usuarios."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from nexusflow.auth.permissions import Role

PASSWORD_MIN_LENGTH = 12
# Límite superior: el hash tiene coste; sin límite, una contraseña de MB es un vector de DoS.
PASSWORD_MAX_LENGTH = 128


def _normalize_email(value: str) -> str:
    return value.strip().lower()


NormalizedEmail = Annotated[EmailStr, AfterValidator(_normalize_email)]


class _StrictModel(BaseModel):
    # Rechaza campos desconocidos: evita "mass assignment" (p. ej. enviar "role": "admin").
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(_StrictModel):
    email: NormalizedEmail
    password: SecretStr = Field(
        description=f"Entre {PASSWORD_MIN_LENGTH} y {PASSWORD_MAX_LENGTH} caracteres"
    )

    @model_validator(mode="after")
    def _password_policy(self) -> Self:
        password = self.password.get_secret_value()
        if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
            raise ValueError(
                f"La contraseña debe tener entre {PASSWORD_MIN_LENGTH} y "
                f"{PASSWORD_MAX_LENGTH} caracteres"
            )
        if password.strip() != password:
            raise ValueError("La contraseña no puede empezar ni terminar con espacios")
        if self.email.split("@")[0] in password.lower():
            raise ValueError("La contraseña no puede contener el nombre de usuario del email")
        if len(set(password)) < 5:
            raise ValueError("La contraseña es demasiado repetitiva")
        return self


class LoginRequest(_StrictModel):
    email: NormalizedEmail
    # Sin política de complejidad al iniciar sesión: solo se acota la longitud.
    password: SecretStr

    @field_validator("password")
    @classmethod
    def _bounded(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= PASSWORD_MAX_LENGTH:
            raise ValueError(f"La contraseña debe tener entre 1 y {PASSWORD_MAX_LENGTH} caracteres")
        return value


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105  # tipo OAuth2, no un secreto
    expires_in: int = Field(description="Segundos hasta que expira el access token")


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class MeResponse(UserResponse):
    permissions: list[str]


class UserUpdateRequest(_StrictModel):
    role: Role | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if self.role is None and self.is_active is None:
            raise ValueError("Indica al menos un campo: role o is_active")
        return self


class UserPage(BaseModel):
    items: list[UserResponse]
    total: int
    limit: int
    offset: int
