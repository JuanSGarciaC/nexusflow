"""Casos de uso de autenticación y gestión de usuarios.

Cada función recibe la sesión de base de datos y hace commit ella misma: un caso de uso
es una transacción, y la auditoría se confirma en la misma transacción.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import NoReturn

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexusflow.audit.service import AuditAction, RequestContext, record
from nexusflow.auth.models import RefreshToken, User
from nexusflow.auth.passwords import (
    burn_verification_time,
    hash_password,
    needs_rehash,
    verify_password,
)
from nexusflow.auth.permissions import Role
from nexusflow.auth.schemas import LoginRequest, RegisterRequest, UserUpdateRequest
from nexusflow.auth.tokens import (
    AccessToken,
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from nexusflow.core.config import Settings
from nexusflow.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from nexusflow.core.logging import get_logger
from nexusflow.db.types import utcnow

logger = get_logger(__name__)

INVALID_CREDENTIALS = "Email o contraseña incorrectos."
INVALID_SESSION = "La sesión no es válida. Inicia sesión de nuevo."


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    user: User
    access: AccessToken
    refresh_token: str


# --------------------------------------------------------------------------- registro


async def register_user(
    session: AsyncSession, settings: Settings, data: RegisterRequest, context: RequestContext
) -> User:
    if not settings.registration_enabled:
        raise ForbiddenError("El registro público está deshabilitado.")

    password_hash = await asyncio.to_thread(hash_password, data.password.get_secret_value())
    # El rol nunca viene del cliente: todo registro público empieza como viewer.
    user = User(email=data.email, password_hash=password_hash, role=Role.VIEWER)
    session.add(user)
    try:
        # Se confía en la restricción UNIQUE y no en un SELECT previo: comprobar y luego
        # insertar tiene una condición de carrera entre dos registros simultáneos.
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Ya existe una cuenta con ese email.") from exc

    record(
        session,
        action=AuditAction.REGISTER,
        context=context,
        actor_user_id=user.id,
        resource_type="user",
        resource_id=str(user.id),
    )
    await session.commit()
    logger.info("user_registered", user_id=str(user.id))
    return user


# --------------------------------------------------------------------------- login


async def authenticate(
    session: AsyncSession, settings: Settings, data: LoginRequest, context: RequestContext
) -> IssuedTokens:
    password = data.password.get_secret_value()
    user = await session.scalar(select(User).where(User.email == data.email))

    if user is None:
        # Argon2 es costoso en CPU: se ejecuta en un hilo para no bloquear el event loop.
        await asyncio.to_thread(burn_verification_time, password)
        await _login_failed(session, context, email=data.email, reason="unknown_email")

    valid = await asyncio.to_thread(verify_password, user.password_hash, password)
    if not valid:
        await _login_failed(session, context, email=data.email, reason="bad_password", user=user)
    if not user.is_active:
        # Mismo mensaje que una contraseña errónea: no se revela que la cuenta existe.
        await _login_failed(session, context, email=data.email, reason="inactive", user=user)

    if needs_rehash(user.password_hash):
        user.password_hash = await asyncio.to_thread(hash_password, password)
    user.last_login_at = utcnow()

    refresh_token, _ = _new_refresh_token(session, settings, user.id, family_id=uuid.uuid4())
    record(
        session,
        action=AuditAction.LOGIN_SUCCEEDED,
        context=context,
        actor_user_id=user.id,
        resource_type="user",
        resource_id=str(user.id),
    )
    await session.commit()
    return IssuedTokens(user, create_access_token(user.id, settings), refresh_token)


async def _login_failed(
    session: AsyncSession,
    context: RequestContext,
    *,
    email: str,
    reason: str,
    user: User | None = None,
) -> NoReturn:
    record(
        session,
        action=AuditAction.LOGIN_FAILED,
        context=context,
        actor_user_id=user.id if user else None,
        resource_type="user",
        resource_id=str(user.id) if user else None,
        details={"email": email, "reason": reason},
    )
    await session.commit()
    logger.info("login_failed", reason=reason)
    raise UnauthorizedError(INVALID_CREDENTIALS)


# --------------------------------------------------------------------------- refresh


def _new_refresh_token(
    session: AsyncSession, settings: Settings, user_id: uuid.UUID, *, family_id: uuid.UUID
) -> tuple[str, uuid.UUID]:
    """Crea un refresh token. Devuelve el valor en claro (solo para el cliente) y su id."""
    raw = generate_refresh_token()
    token_id = uuid.uuid4()
    session.add(
        RefreshToken(
            id=token_id,
            user_id=user_id,
            family_id=family_id,
            token_hash=hash_refresh_token(raw),
            expires_at=utcnow() + timedelta(days=settings.refresh_token_ttl_days),
        )
    )
    return raw, token_id


async def _revoke_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


async def rotate_refresh_token(
    session: AsyncSession, settings: Settings, raw_token: str, context: RequestContext
) -> IssuedTokens:
    """Canjea un refresh token por uno nuevo (rotación) y un access token.

    `FOR UPDATE` serializa los canjes concurrentes del mismo token: el segundo ve el token
    ya rotado y se trata como reutilización. Es la opción estricta (sin periodo de gracia):
    ante la duda, se cierra la sesión.
    """
    current = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_refresh_token(raw_token))
        .with_for_update()
    )
    if current is None:
        raise UnauthorizedError(INVALID_SESSION)

    if current.revoked_at is not None:
        if current.replaced_by_id is not None:
            # Un token ya rotado vuelve a aparecer: alguien tiene una copia. Se revoca
            # toda la familia, lo que también expulsa al atacante si fue el primero en usarlo.
            await _revoke_family(session, current.family_id)
            record(
                session,
                action=AuditAction.REFRESH_REUSE_DETECTED,
                context=context,
                actor_user_id=current.user_id,
                resource_type="refresh_token_family",
                resource_id=str(current.family_id),
            )
            await session.commit()
            logger.warning("refresh_token_reuse_detected", user_id=str(current.user_id))
        else:
            await session.rollback()
        raise UnauthorizedError(INVALID_SESSION)

    if current.expires_at <= utcnow():
        await session.rollback()
        raise UnauthorizedError(INVALID_SESSION)

    user = await session.get(User, current.user_id)
    if user is None or not user.is_active:
        await _revoke_family(session, current.family_id)
        await session.commit()
        raise UnauthorizedError(INVALID_SESSION)

    new_raw, new_id = _new_refresh_token(session, settings, user.id, family_id=current.family_id)
    # Se inserta el nuevo token antes de enlazarlo: la FK replaced_by_id debe existir.
    await session.flush()
    current.revoked_at = utcnow()
    current.replaced_by_id = new_id
    await session.commit()
    return IssuedTokens(user, create_access_token(user.id, settings), new_raw)


async def logout(session: AsyncSession, raw_token: str | None, context: RequestContext) -> None:
    """Cierra la sesión revocando la familia del token. Idempotente."""
    if not raw_token:
        return
    current = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
    )
    if current is None:
        return
    await _revoke_family(session, current.family_id)
    record(
        session,
        action=AuditAction.LOGOUT,
        context=context,
        actor_user_id=current.user_id,
        resource_type="refresh_token_family",
        resource_id=str(current.family_id),
    )
    await session.commit()


# --------------------------------------------------------------------------- usuarios


async def get_active_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError(INVALID_SESSION)
    return user


async def list_users(session: AsyncSession, *, limit: int, offset: int) -> tuple[list[User], int]:
    total = await session.scalar(select(func.count()).select_from(User)) or 0
    rows = await session.scalars(
        select(User).order_by(User.created_at, User.id).limit(limit).offset(offset)
    )
    return list(rows), total


async def update_user(
    session: AsyncSession,
    *,
    actor: User,
    user_id: uuid.UUID,
    data: UserUpdateRequest,
    context: RequestContext,
) -> User:
    if actor.id == user_id:
        # Evita que un administrador se quite a sí mismo el acceso por error y que el
        # sistema quede sin administradores.
        raise ForbiddenError("No puedes modificar tu propio rol ni tu propio estado.")

    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise NotFoundError("El usuario no existe.")

    if data.role is not None and data.role != user.role:
        record(
            session,
            action=AuditAction.USER_ROLE_CHANGED,
            context=context,
            actor_user_id=actor.id,
            resource_type="user",
            resource_id=str(user.id),
            details={"from": user.role.value, "to": data.role.value},
        )
        user.role = data.role

    if data.is_active is not None and data.is_active != user.is_active:
        user.is_active = data.is_active
        record(
            session,
            action=AuditAction.USER_ACTIVATED if data.is_active else AuditAction.USER_DEACTIVATED,
            context=context,
            actor_user_id=actor.id,
            resource_type="user",
            resource_id=str(user.id),
        )
        if not data.is_active:
            # Desactivar debe cerrar también las sesiones abiertas, no solo impedir logins.
            await revoke_all_for_user(session, user.id)

    await session.commit()
    await session.refresh(user)
    return user
