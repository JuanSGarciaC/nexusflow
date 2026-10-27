"""Tokens de acceso (JWT) y de refresco (opacos).

- Access token: JWT HS256 de vida corta. Solo lleva `sub`; el rol y el estado del usuario
  se leen de la base de datos en cada petición, así un cambio de rol o una desactivación
  surten efecto de inmediato y no 15 minutos después.
- Refresh token: valor aleatorio opaco (no JWT). En la base de datos solo se guarda su
  SHA-256: una filtración de la tabla no permite usar los tokens.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt

from nexusflow.core.config import Settings
from nexusflow.core.errors import UnauthorizedError
from nexusflow.db.types import utcnow

ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"  # noqa: S105  # no es una contraseña, es el tipo de token


@dataclass(frozen=True, slots=True)
class AccessToken:
    token: str
    expires_at: datetime


def create_access_token(
    user_id: uuid.UUID, settings: Settings, *, now: datetime | None = None
) -> AccessToken:
    issued_at = now or utcnow()
    expires_at = issued_at + timedelta(minutes=settings.access_token_ttl_minutes)
    payload = {
        "sub": str(user_id),
        "type": ACCESS_TOKEN_TYPE,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": expires_at,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=ALGORITHM)
    return AccessToken(token=token, expires_at=expires_at)


def decode_access_token(token: str, settings: Settings) -> uuid.UUID:
    """Valida firma, algoritmo, emisor, audiencia y vigencia. Devuelve el id del usuario."""
    try:
        payload = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            # Lista explícita: impide ataques de confusión de algoritmo y el "alg: none".
            algorithms=[ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "nbf", "sub", "iss", "aud", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("El token ha expirado.") from exc
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Token inválido.") from exc

    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise UnauthorizedError("Token inválido.")
    try:
        return uuid.UUID(payload["sub"])
    except (ValueError, TypeError) as exc:
        raise UnauthorizedError("Token inválido.") from exc


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits de entropía


def hash_refresh_token(token: str) -> str:
    # SHA-256 sin sal es adecuado aquí: el token ya tiene 256 bits aleatorios, así que no
    # hay diccionario que atacar (al contrario que con las contraseñas).
    return hashlib.sha256(token.encode()).hexdigest()
