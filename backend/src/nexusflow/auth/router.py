"""Endpoints de autenticación y gestión de usuarios."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Query, Response, status

from nexusflow.auth import service
from nexusflow.auth.dependencies import (
    ContextDep,
    CurrentUser,
    RateLimiterDep,
    SessionDep,
    SettingsDep,
    require_permission,
)
from nexusflow.auth.models import User
from nexusflow.auth.permissions import ROLE_PERMISSIONS, Permission
from nexusflow.auth.schemas import (
    LoginRequest,
    MeResponse,
    RegisterRequest,
    TokenResponse,
    UserPage,
    UserResponse,
    UserUpdateRequest,
)
from nexusflow.core.config import Settings
from nexusflow.core.errors import UnauthorizedError
from nexusflow.core.rate_limit import RateLimit

REFRESH_COOKIE = "nexusflow_refresh"
# La cookie solo viaja a las rutas de autenticación, no a toda la API.
REFRESH_COOKIE_PATH = "/api/v1/auth"

auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
users_router = APIRouter(prefix="/api/v1/users", tags=["users"])

_PROBLEM: dict[str, object] = {"content": {"application/problem+json": {}}}
_AUTH_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"description": "No autenticado", **_PROBLEM},
    429: {"description": "Límite de intentos superado", **_PROBLEM},
}


def _rules(settings: Settings) -> dict[str, RateLimit]:
    return {
        "login_ip": RateLimit("login_ip", settings.login_rate_limit_per_ip, 60),
        "login_email": RateLimit("login_email", settings.login_rate_limit_per_email, 15 * 60),
        "register_ip": RateLimit("register_ip", settings.register_rate_limit_per_ip, 60 * 60),
        "refresh_ip": RateLimit("refresh_ip", 60, 60),
    }


def _set_refresh_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        path=REFRESH_COOKIE_PATH,
        httponly=True,  # inaccesible para JavaScript: un XSS no puede robarla
        secure=settings.cookie_secure,
        samesite="strict",  # no se envía en peticiones iniciadas desde otros sitios (CSRF)
    )


def _token_response(settings: Settings, access_token: str) -> TokenResponse:
    return TokenResponse(
        access_token=access_token, expires_in=settings.access_token_ttl_minutes * 60
    )


@auth_router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar una cuenta (rol viewer)",
    responses={409: {"description": "Email ya registrado", **_PROBLEM}, **_AUTH_ERRORS},
)
async def register(
    data: RegisterRequest,
    session: SessionDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    context: ContextDep,
) -> User:
    await limiter.hit(_rules(settings)["register_ip"], context.ip_address or "unknown")
    return await service.register_user(session, settings, data, context)


@auth_router.post(
    "/login",
    response_model=TokenResponse,
    summary="Iniciar sesión",
    description="Devuelve un access token y fija el refresh token en una cookie httpOnly.",
    responses=_AUTH_ERRORS,
)
async def login(
    data: LoginRequest,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    context: ContextDep,
) -> TokenResponse:
    rules = _rules(settings)
    # Dos límites: por IP (un atacante, muchas cuentas) y por cuenta (muchas IPs, una cuenta).
    await limiter.hit(rules["login_ip"], context.ip_address or "unknown")
    await limiter.hit(rules["login_email"], data.email)
    issued = await service.authenticate(session, settings, data, context)
    await limiter.reset(rules["login_email"], data.email)
    _set_refresh_cookie(response, settings, issued.refresh_token)
    return _token_response(settings, issued.access.token)


@auth_router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Renovar el access token",
    description="Canjea la cookie de refresh por un access token nuevo y rota la cookie.",
    responses=_AUTH_ERRORS,
)
async def refresh(
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    context: ContextDep,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE, max_length=128)] = None,
) -> TokenResponse:
    await limiter.hit(_rules(settings)["refresh_ip"], context.ip_address or "unknown")
    if not refresh_token:
        raise UnauthorizedError(service.INVALID_SESSION)
    issued = await service.rotate_refresh_token(session, settings, refresh_token, context)
    _set_refresh_cookie(response, settings, issued.refresh_token)
    return _token_response(settings, issued.access.token)


@auth_router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cerrar sesión",
    description="Revoca la familia de refresh tokens. Idempotente.",
)
async def logout(
    session: SessionDep,
    context: ContextDep,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE, max_length=128)] = None,
) -> Response:
    await service.logout(session, refresh_token, context)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)
    return response


@users_router.get("/me", response_model=MeResponse, summary="Usuario autenticado")
async def me(user: CurrentUser) -> MeResponse:
    base = UserResponse.model_validate(user)
    return MeResponse(
        **base.model_dump(),
        permissions=sorted(p.value for p in ROLE_PERMISSIONS[user.role]),
    )


@users_router.get(
    "",
    response_model=UserPage,
    summary="Listar usuarios",
    dependencies=[Depends(require_permission(Permission.USERS_READ))],
)
async def list_users(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> UserPage:
    users, total = await service.list_users(session, limit=limit, offset=offset)
    return UserPage(
        items=[UserResponse.model_validate(u) for u in users],
        total=total,
        limit=limit,
        offset=offset,
    )


@users_router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Cambiar rol o estado de un usuario",
    responses={
        403: {"description": "Sin permiso o intento de modificarse a sí mismo", **_PROBLEM},
        404: {"description": "El usuario no existe", **_PROBLEM},
    },
)
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdateRequest,
    session: SessionDep,
    context: ContextDep,
    actor: Annotated[User, Depends(require_permission(Permission.USERS_MANAGE))],
) -> User:
    return await service.update_user(
        session, actor=actor, user_id=user_id, data=data, context=context
    )
