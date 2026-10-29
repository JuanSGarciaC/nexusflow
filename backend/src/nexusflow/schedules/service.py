"""Casos de uso de programaciones y el disparador que las ejecuta.

Garantías del disparador (`ScheduleFirer`):
- Cada disparo lleva una `idempotency_key` derivada de (programación, instante previsto).
  Si el proceso muere entre crear la ejecución y avanzar `next_run_at`, el siguiente barrido
  repite el disparo y `create_run` devuelve la misma ejecución: nunca hay dos.
- `next_run_at` solo avanza si todavía vale el valor leído (UPDATE condicional): varios
  barridos simultáneos no se pisan.
- Un disparo con más retraso que `schedule_misfire_grace_seconds` se omite, y se avisa en
  `last_error`; no se acumulan ejecuciones atrasadas.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexusflow.audit.service import RequestContext, record
from nexusflow.auth.models import User
from nexusflow.core.config import Settings
from nexusflow.core.errors import AppError, NotFoundError
from nexusflow.core.logging import get_logger
from nexusflow.db.types import utcnow
from nexusflow.engine import service as runs
from nexusflow.engine.dispatch import Dispatcher
from nexusflow.engine.schemas import RunCreate
from nexusflow.schedules.cron import InvalidScheduleError, next_fire
from nexusflow.schedules.models import WorkflowSchedule
from nexusflow.schedules.schemas import ScheduleCreate, ScheduleUpdate
from nexusflow.workflows.models import Workflow

logger = get_logger(__name__)


class InvalidScheduleRequestError(AppError):
    status_code = 422
    code = "invalid-schedule"
    title = "La programación no es válida"


class ScheduleAction:
    CREATED = "schedule.created"
    UPDATED = "schedule.updated"
    DELETED = "schedule.deleted"


def _audit(
    session: AsyncSession,
    action: str,
    actor: User,
    context: RequestContext,
    schedule_id: uuid.UUID,
    details: dict[str, Any] | None = None,
) -> None:
    record(
        session,
        action=action,
        context=context,
        actor_user_id=actor.id,
        resource_type="workflow_schedule",
        resource_id=str(schedule_id),
        details=details,
    )


async def _schedule(session: AsyncSession, schedule_id: uuid.UUID) -> WorkflowSchedule:
    schedule = await session.get(WorkflowSchedule, schedule_id, with_for_update=True)
    if schedule is None:
        raise NotFoundError("La programación no existe.")
    return schedule


async def create_schedule(
    session: AsyncSession,
    actor: User,
    workflow_id: uuid.UUID,
    data: ScheduleCreate,
    context: RequestContext,
) -> WorkflowSchedule:
    if await session.get(Workflow, workflow_id) is None:
        raise NotFoundError("El flujo no existe.")
    schedule = WorkflowSchedule(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        name=data.name,
        cron_expression=data.cron_expression,
        timezone=data.timezone,
        input=dict(data.input),
        is_active=data.is_active,
        next_run_at=(
            next_fire(data.cron_expression, data.timezone, utcnow()) if data.is_active else None
        ),
        created_by=actor.id,
    )
    session.add(schedule)
    _audit(
        session,
        ScheduleAction.CREATED,
        actor,
        context,
        schedule.id,
        {"workflow_id": str(workflow_id), "cron": data.cron_expression, "tz": data.timezone},
    )
    await session.commit()
    await session.refresh(schedule)
    return schedule


async def list_schedules(session: AsyncSession, workflow_id: uuid.UUID) -> list[WorkflowSchedule]:
    if await session.get(Workflow, workflow_id) is None:
        raise NotFoundError("El flujo no existe.")
    rows = await session.scalars(
        select(WorkflowSchedule)
        .where(WorkflowSchedule.workflow_id == workflow_id)
        .order_by(WorkflowSchedule.created_at)
    )
    return list(rows)


async def update_schedule(
    session: AsyncSession,
    actor: User,
    schedule_id: uuid.UUID,
    data: ScheduleUpdate,
    context: RequestContext,
) -> WorkflowSchedule:
    schedule = await _schedule(session, schedule_id)
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    for key, value in changes.items():
        setattr(schedule, key, value)
    # Cualquier cambio de horario o la reactivación recalculan el próximo disparo desde ahora.
    if schedule.is_active:
        if "cron_expression" in changes or "timezone" in changes or schedule.next_run_at is None:
            try:
                schedule.next_run_at = next_fire(
                    schedule.cron_expression, schedule.timezone, utcnow()
                )
            except InvalidScheduleError as exc:
                await session.rollback()
                raise InvalidScheduleRequestError(str(exc)) from exc
    else:
        schedule.next_run_at = None
    _audit(
        session, ScheduleAction.UPDATED, actor, context, schedule_id, {"changed": sorted(changes)}
    )
    await session.commit()
    await session.refresh(schedule)
    return schedule


async def delete_schedule(
    session: AsyncSession, actor: User, schedule_id: uuid.UUID, context: RequestContext
) -> None:
    schedule = await _schedule(session, schedule_id)
    await session.delete(schedule)
    _audit(session, ScheduleAction.DELETED, actor, context, schedule_id)
    await session.commit()


# ------------------------------------------------------------------ disparador


@dataclass
class ScheduleReport:
    fired: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def fire_key(schedule_id: uuid.UUID, due: datetime) -> str:
    return f"sched:{schedule_id.hex[:12]}:{due:%Y%m%dT%H%M%S}"


class ScheduleFirer:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        dispatcher: Dispatcher,
    ) -> None:
        self._sessions = sessions
        self._settings = settings
        self._dispatcher = dispatcher

    async def sweep(self, *, limit: int = 100) -> ScheduleReport:
        report = ScheduleReport()
        now = utcnow()
        async with self._sessions() as session:
            due = (
                await session.execute(
                    select(WorkflowSchedule.id, WorkflowSchedule.next_run_at)
                    .where(WorkflowSchedule.is_active, WorkflowSchedule.next_run_at <= now)
                    .order_by(WorkflowSchedule.next_run_at)
                    .limit(limit)
                )
            ).all()
        for schedule_id, due_at in due:
            if due_at is None:  # pragma: no cover - el filtro lo excluye
                continue
            try:
                outcome = await self._fire(schedule_id, due_at, now)
            except Exception as exc:  # una programación rota no debe frenar a las demás
                logger.exception("schedule_fire_crashed", schedule_id=str(schedule_id))
                report.errors.append(f"{schedule_id}: {type(exc).__name__}")
                continue
            if outcome == "fired":
                report.fired += 1
            elif outcome == "skipped":
                report.skipped += 1
            elif outcome == "failed":
                report.failed += 1
        return report

    async def _fire(self, schedule_id: uuid.UUID, due_at: datetime, now: datetime) -> str:
        run_id: uuid.UUID | None = None
        error: str | None = None
        outcome = "fired"
        async with self._sessions() as session:
            schedule = await session.get(WorkflowSchedule, schedule_id)
            if schedule is None or not schedule.is_active or schedule.next_run_at != due_at:
                return "noop"  # otro barrido ya la procesó
            expression, timezone = schedule.cron_expression, schedule.timezone
            late = (now - due_at).total_seconds()
            if late > self._settings.schedule_misfire_grace_seconds:
                outcome, error = (
                    "skipped",
                    f"Disparo de {due_at:%Y-%m-%d %H:%M} UTC omitido ({int(late)} s de retraso).",
                )
            else:
                try:
                    created = await runs.create_run(
                        session,
                        self._settings,
                        workflow_id=schedule.workflow_id,
                        data=RunCreate(
                            input=dict(schedule.input),
                            idempotency_key=fire_key(schedule_id, due_at),
                        ),
                        actor_id=None,
                        trigger_type="schedule",
                        context=RequestContext(request_id=f"schedule:{schedule_id}"),
                        strict_input=True,
                    )
                    run_id = created.run.id
                    await self._dispatcher.dispatch(created.dispatch)
                except AppError as exc:
                    await session.rollback()
                    outcome, error = "failed", (exc.detail or exc.title)[:300]
        async with self._sessions() as session:
            result = await session.execute(
                update(WorkflowSchedule)
                .where(WorkflowSchedule.id == schedule_id, WorkflowSchedule.next_run_at == due_at)
                .values(
                    next_run_at=next_fire(expression, timezone, max(now, due_at)),
                    last_run_at=now if outcome == "fired" else WorkflowSchedule.last_run_at,
                    last_run_id=run_id if run_id else WorkflowSchedule.last_run_id,
                    last_error=error,
                )
            )
            await session.commit()
        if result.rowcount == 0:  # type: ignore[attr-defined]
            return "noop"
        if error:
            logger.warning("schedule_not_fired", schedule_id=str(schedule_id), reason=error)
        return outcome
