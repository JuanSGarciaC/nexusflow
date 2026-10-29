"""Endpoints de programaciones (requieren `schedules:manage`)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from nexusflow.auth.dependencies import ContextDep, SessionDep, require_permission
from nexusflow.auth.models import User
from nexusflow.auth.permissions import Permission
from nexusflow.schedules import service
from nexusflow.schedules.schemas import ScheduleCreate, ScheduleUpdate, ScheduleView

router = APIRouter(prefix="/api/v1", tags=["programaciones"])

Manager = Annotated[User, Depends(require_permission(Permission.SCHEDULES_MANAGE))]


@router.post(
    "/workflows/{workflow_id}/schedules",
    response_model=ScheduleView,
    status_code=status.HTTP_201_CREATED,
    summary="Programar un flujo con una expresión cron",
    description=(
        "Cron de 5 campos evaluado en la zona horaria indicada. Cada disparo crea una "
        "ejecución con la última versión publicada. Si el servicio estuvo caído, los disparos "
        "perdidos que superen el margen de gracia se omiten, no se recuperan."
    ),
)
async def create_schedule(
    workflow_id: uuid.UUID,
    data: ScheduleCreate,
    session: SessionDep,
    context: ContextDep,
    actor: Manager,
) -> ScheduleView:
    schedule = await service.create_schedule(session, actor, workflow_id, data, context)
    return ScheduleView.model_validate(schedule)


@router.get(
    "/workflows/{workflow_id}/schedules",
    response_model=list[ScheduleView],
    summary="Programaciones de un flujo",
)
async def list_schedules(
    workflow_id: uuid.UUID, session: SessionDep, _: Manager
) -> list[ScheduleView]:
    rows = await service.list_schedules(session, workflow_id)
    return [ScheduleView.model_validate(r) for r in rows]


@router.patch(
    "/schedules/{schedule_id}",
    response_model=ScheduleView,
    summary="Cambiar horario, entrada o pausar/reanudar",
)
async def update_schedule(
    schedule_id: uuid.UUID,
    data: ScheduleUpdate,
    session: SessionDep,
    context: ContextDep,
    actor: Manager,
) -> ScheduleView:
    schedule = await service.update_schedule(session, actor, schedule_id, data, context)
    return ScheduleView.model_validate(schedule)


@router.delete(
    "/schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar una programación",
)
async def delete_schedule(
    schedule_id: uuid.UUID, session: SessionDep, context: ContextDep, actor: Manager
) -> Response:
    await service.delete_schedule(session, actor, schedule_id, context)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
