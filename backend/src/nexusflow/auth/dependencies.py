"""Dependencias FastAPI de autenticación y autorización."""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from nexusflow.audit.service import RequestContext
from nexusflow.auth import service
from nexusflow.auth.models import User
from nexusflow.auth.permissions import Permission, has_permission
from nexusflow.auth.tokens import decode_access_token
from nexusflow.core.config import Settings
from nexusflow.core.errors import ForbiddenError, UnauthorizedError
from nexusflow.core.rate_limit import RateLimiter
from nexusflow.db.session import get_session

# auto_error=False: la ausencia de token se convierte en nuestro 401 RFC 9457 en lugar del
# 403 por defecto de FastAPI. Se mantiene para documentar el esquema en OpenAPI.
_bearer = HTTPBearer(auto_error=False, description="Access token JWT obtenido en /auth/login")


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


def client_ip(request: Request) -> str | None:
    """IP del cliente, o None si el host no es una IP válida (socket Unix, nombre, etc.).

    Detrás de un proxy inverso, uvicorn debe arrancarse con --proxy-headers y
    --forwarded-allow-ips limitado a la IP del proxy; si no, esta será la IP del proxy.
    """
    host = request.client.host if request.client else None
    if host is None:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def get_request_context(request: Request) -> RequestContext:
    return RequestContext(
        ip_address=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
ContextDep = Annotated[RequestContext, Depends(get_request_context)]


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("Falta el token de acceso.")
    user_id = decode_access_token(credentials.credentials, settings)
    # Se consulta la base de datos en cada petición: rol y estado siempre actuales.
    return await service.get_active_user(session, user_id)


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_permission(permission: Permission) -> Callable[[User], Awaitable[User]]:
    async def _check(user: CurrentUser) -> User:
        if not has_permission(user.role, permission):
            raise ForbiddenError(f"Se requiere el permiso '{permission.value}'.")
        return user

    return _check
