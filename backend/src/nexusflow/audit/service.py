"""Escritura de eventos de auditoría.

El evento se añade a la sesión del caso de uso, de modo que se confirma en la misma
transacción que el cambio que describe: no puede haber un cambio sin su registro, ni un
registro de un cambio que se deshizo.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nexusflow.audit.models import AuditLog


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Datos de la petición que interesan a la auditoría."""

    ip_address: str | None = None
    request_id: str | None = None


class AuditAction:
    REGISTER = "auth.register"
    LOGIN_SUCCEEDED = "auth.login.succeeded"
    LOGIN_FAILED = "auth.login.failed"
    LOGOUT = "auth.logout"
    REFRESH_REUSE_DETECTED = "auth.refresh.reuse_detected"
    USER_ROLE_CHANGED = "user.role_changed"
    USER_ACTIVATED = "user.activated"
    USER_DEACTIVATED = "user.deactivated"
    USER_CREATED_BY_CLI = "user.created_by_cli"


def record(
    session: AsyncSession,
    *,
    action: str,
    context: RequestContext,
    actor_user_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            action=action,
            actor_user_id=actor_user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=context.ip_address,
            request_id=context.request_id,
            details=details or {},
        )
    )
