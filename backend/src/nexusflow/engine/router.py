"""Endpoints de ejecuciones."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from nexusflow.auth.dependencies import ContextDep, SessionDep, SettingsDep, require_permission
from nexusflow.auth.models import User
from nexusflow.auth.permissions import Permission
from nexusflow.engine import service
from nexusflow.engine.dispatch import Dispatcher
from nexusflow.engine.models import RunStatus, WorkflowRun
from nexusflow.engine.schemas import (
    AttemptView,
    ResolveStep,
    RunCreate,
    RunDetail,
    RunPage,
    RunSummary,
    StepView,
)
from nexusflow.webhooks.outbox import pop_new_deliveries
from nexusflow.workflows.dsl import STEP_ID_PATTERN

router = APIRouter(prefix="/api/v1", tags=["runs"])

_PROBLEM: dict[str, object] = {"content": {"application/problem+json": {}}}
Executor = Annotated[User, Depends(require_permission(Permission.WORKFLOWS_EXECUTE))]
Reader = Annotated[User, Depends(require_permission(Permission.RUNS_READ))]


def get_dispatcher(request: Request) -> Dispatcher:
    dispatcher: Dispatcher = request.app.state.dispatcher
    return dispatcher


DispatcherDep = Annotated[Dispatcher, Depends(get_dispatcher)]


def _duration_ms(run: WorkflowRun) -> int | None:
    if run.started_at and run.finished_at:
        return int((run.finished_at - run.started_at).total_seconds() * 1000)
    return None


def summary(run: WorkflowRun, version: int) -> RunSummary:
    return RunSummary(
        id=run.id,
        workflow_id=run.workflow_id,
        version_id=run.version_id,
        version=version,
        status=run.status,
        trigger_type=run.trigger_type,
        triggered_by=run.triggered_by,
        idempotency_key=run.idempotency_key,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_ms=_duration_ms(run),
        error=run.error,
    )


async def detail(session: AsyncSession, run_id: uuid.UUID) -> RunDetail:
    # Lectura fresca: el worker pudo cambiar la ejecución después de nuestro commit.
    session.expire_all()
    run = await service.get_run(session, run_id)
    steps = await service.run_steps(session, run_id)
    return RunDetail(
        **summary(run, await service.version_number(session, run.version_id)).model_dump(),
        input=run.input,
        output=run.output,
        deadline_at=run.deadline_at,
        steps=[
            StepView.model_validate(step).model_copy(
                update={"attempts": [AttemptView.model_validate(a) for a in attempts]}
            )
            for step, attempts in steps
        ],
    )


@router.post(
    "/workflows/{workflow_id}/runs",
    response_model=RunDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ejecutar un flujo",
    description=(
        "Crea la ejecución y la encola (202). Con `idempotency_key`, repetir la petición con "
        "la misma entrada devuelve la ejecución existente (200) en lugar de crear otra."
    ),
    responses={
        200: {"model": RunDetail, "description": "Ejecución existente (idempotencia)"},
        409: {"description": "Sin versión publicada, archivado o clave reutilizada", **_PROBLEM},
        422: {"description": "Entrada no válida", **_PROBLEM},
    },
)
async def create_run(
    workflow_id: uuid.UUID,
    data: RunCreate,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    dispatcher: DispatcherDep,
    context: ContextDep,
    actor: Executor,
) -> RunDetail:
    created = await service.create_run(
        session,
        settings,
        workflow_id=workflow_id,
        data=data,
        actor_id=actor.id,
        trigger_type="manual",
        context=context,
    )
    await dispatcher.dispatch(created.dispatch)
    if not created.created:
        response.status_code = status.HTTP_200_OK
    return await detail(session, created.run.id)


@router.get("/runs", response_model=RunPage, summary="Historial de ejecuciones")
async def list_runs(
    session: SessionDep,
    _: Reader,
    workflow_id: uuid.UUID | None = None,
    status_filter: Annotated[RunStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> RunPage:
    rows, total = await service.list_runs(
        session, workflow_id=workflow_id, status=status_filter, limit=limit, offset=offset
    )
    return RunPage(
        items=[summary(run, number) for run, number in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/runs/{run_id}",
    response_model=RunDetail,
    summary="Detalle de una ejecución con pasos, intentos y errores",
)
async def get_run(run_id: uuid.UUID, session: SessionDep, _: Reader) -> RunDetail:
    return await detail(session, run_id)


@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunDetail,
    summary="Cancelar una ejecución",
    responses={409: {"description": "Ya terminó", **_PROBLEM}},
)
async def cancel_run(
    run_id: uuid.UUID,
    session: SessionDep,
    dispatcher: DispatcherDep,
    context: ContextDep,
    actor: Executor,
) -> RunDetail:
    await service.cancel_run(session, actor.id, run_id, context)
    await dispatcher.dispatch(pop_new_deliveries(session))
    return await detail(session, run_id)


@router.post(
    "/runs/{run_id}/steps/{step_id}/resolve",
    response_model=RunDetail,
    summary="Resolver un paso en revisión manual",
    responses={409: {"description": "El paso no está en revisión", **_PROBLEM}},
)
async def resolve_step(
    run_id: uuid.UUID,
    step_id: Annotated[str, Path(pattern=STEP_ID_PATTERN)],
    data: ResolveStep,
    session: SessionDep,
    settings: SettingsDep,
    dispatcher: DispatcherDep,
    context: ContextDep,
    actor: Executor,
) -> RunDetail:
    _, items = await service.resolve_step(
        session,
        settings,
        actor_id=actor.id,
        run_id=run_id,
        step_id=step_id,
        data=data,
        context=context,
    )
    await dispatcher.dispatch(items + pop_new_deliveries(session))
    return await detail(session, run_id)
