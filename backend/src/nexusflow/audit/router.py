"""Consulta del registro de auditoría (solo lectura, permiso audit:read)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from nexusflow.audit.models import AuditLog
from nexusflow.auth.dependencies import SessionDep, require_permission
from nexusflow.auth.models import User
from nexusflow.auth.permissions import Permission

router = APIRouter(prefix="/api/v1/audit-logs", tags=["audit"])
Auditor = Annotated[User, Depends(require_permission(Permission.AUDIT_READ))]


class AuditEntry(BaseModel):
    id: int
    occurred_at: datetime
    action: str
    actor_user_id: uuid.UUID | None
    actor_email: str | None
    resource_type: str | None
    resource_id: str | None
    ip_address: str | None
    request_id: str | None
    details: dict[str, Any]


class AuditPage(BaseModel):
    items: list[AuditEntry]
    next_before_id: int | None = Field(
        description="Pásalo como before_id para la página siguiente; null si no hay más"
    )


@router.get(
    "",
    response_model=AuditPage,
    summary="Buscar en el registro de auditoría",
    description="Orden descendente por id. Paginación por cursor (before_id): estable "
    "aunque se inserten eventos nuevos mientras se pagina, y sin OFFSET costoso.",
)
async def list_audit_logs(
    session: SessionDep,
    _: Auditor,
    action: Annotated[str | None, Query(max_length=64, description="Acción exacta")] = None,
    action_prefix: Annotated[
        str | None, Query(max_length=64, description="Prefijo, p. ej. 'auth.'")
    ] = None,
    actor_user_id: uuid.UUID | None = None,
    resource_type: Annotated[str | None, Query(max_length=64)] = None,
    resource_id: Annotated[str | None, Query(max_length=64)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    query = (
        select(AuditLog, User.email)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .order_by(AuditLog.id.desc())
        .limit(limit + 1)  # uno de más para saber si hay página siguiente
    )
    if action:
        query = query.where(AuditLog.action == action)
    if action_prefix:
        # autoescape: un '%' o '_' en el filtro se trata como texto, no como comodín.
        query = query.where(AuditLog.action.startswith(action_prefix, autoescape=True))
    if actor_user_id:
        query = query.where(AuditLog.actor_user_id == actor_user_id)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    if resource_id:
        query = query.where(AuditLog.resource_id == resource_id)
    if since:
        query = query.where(AuditLog.occurred_at >= since)
    if until:
        query = query.where(AuditLog.occurred_at < until)
    if before_id:
        query = query.where(AuditLog.id < before_id)

    rows = (await session.execute(query)).all()
    page = rows[:limit]
    items = [
        AuditEntry(
            id=log.id,
            occurred_at=log.occurred_at,
            action=log.action,
            actor_user_id=log.actor_user_id,
            actor_email=email,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            ip_address=str(log.ip_address) if log.ip_address else None,
            request_id=log.request_id,
            details=log.details,
        )
        for log, email in page
    ]
    return AuditPage(items=items, next_before_id=page[-1][0].id if len(rows) > limit else None)
