"""Casos de uso de ejecuciones (lado API): crear, consultar, cancelar y resolver."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexusflow.audit.service import RequestContext, record
from nexusflow.core.config import Settings
from nexusflow.core.errors import ConflictError, NotFoundError
from nexusflow.db.types import utcnow
from nexusflow.engine.dispatch import DispatchItem
from nexusflow.engine.inputs import validate_run_input
from nexusflow.engine.models import (
    RUN_TERMINAL,
    STEP_TERMINAL,
    RunStatus,
    StepAttempt,
    StepRun,
    StepStatus,
    WorkflowRun,
    assert_run_transition,
    assert_step_transition,
)
from nexusflow.engine.planning import advance, dispatch_item, finish_run, new_step_run, succeed_step
from nexusflow.engine.schemas import ResolveStep, RunCreate
from nexusflow.workflows.models import Workflow, WorkflowStatus, WorkflowVersion
from nexusflow.workflows.service import canonical_json, latest_published, parse_stored


class RunAction:
    CREATED = "run.created"
    CANCELLED = "run.cancelled"
    STEP_RESOLVED = "run.step_resolved"


@dataclass(frozen=True, slots=True)
class CreatedRun:
    run: WorkflowRun
    created: bool  # False: devuelta por idempotencia
    dispatch: list[DispatchItem]


def _input_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(data).encode()).hexdigest()


async def _replay(
    session: AsyncSession, workflow_id: uuid.UUID, key: str, input_hash: str
) -> WorkflowRun | None:
    existing = await session.scalar(
        select(WorkflowRun).where(
            WorkflowRun.workflow_id == workflow_id, WorkflowRun.idempotency_key == key
        )
    )
    if existing is not None and existing.input_hash != input_hash:
        raise ConflictError(
            "Esa idempotency_key ya se usó con una entrada distinta. Usa una clave nueva."
        )
    return existing


async def create_run(
    session: AsyncSession,
    settings: Settings,
    *,
    workflow_id: uuid.UUID,
    data: RunCreate,
    actor_id: uuid.UUID | None,
    trigger_type: str,
    context: RequestContext,
) -> CreatedRun:
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise NotFoundError("El flujo no existe.")
    if workflow.status == WorkflowStatus.ARCHIVED:
        raise ConflictError("El flujo está archivado y no se puede ejecutar.")

    if data.version is not None:
        version = await session.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == workflow_id,
                WorkflowVersion.version == data.version,
                WorkflowVersion.published_at.is_not(None),
            )
        )
        if version is None:
            raise NotFoundError("Esa versión no existe o no está publicada.")
    else:
        version = await latest_published(session, workflow_id)
        if version is None:
            raise ConflictError("El flujo no tiene ninguna versión publicada.")

    definition = parse_stored(version.definition)
    run_input = validate_run_input(definition.inputs, dict(data.input))
    digest = _input_hash(run_input)

    if data.idempotency_key:
        existing = await _replay(session, workflow_id, data.idempotency_key, digest)
        if existing is not None:
            return CreatedRun(existing, created=False, dispatch=[])

    now = utcnow()
    run = WorkflowRun(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        version_id=version.id,
        status=RunStatus.PENDING,
        trigger_type=trigger_type,
        triggered_by=actor_id,
        idempotency_key=data.idempotency_key,
        input_hash=digest,
        input=run_input,
        created_at=now,
        deadline_at=now + timedelta(hours=settings.run_max_duration_hours),
    )
    session.add(run)
    try:
        await session.flush()
    except IntegrityError:
        # Dos peticiones simultáneas con la misma clave: gana una, la otra la devuelve.
        await session.rollback()
        if not data.idempotency_key:
            raise
        existing = await _replay(session, workflow_id, data.idempotency_key, digest)
        if existing is None:
            raise
        return CreatedRun(existing, created=False, dispatch=[])

    first = new_step_run(run, definition.step_map()[definition.start], 1, now)
    item = dispatch_item(first, now, settings)
    session.add(first)
    record(
        session,
        action=RunAction.CREATED,
        context=context,
        actor_user_id=actor_id,
        resource_type="workflow_run",
        resource_id=str(run.id),
        details={
            "workflow_id": str(workflow_id),
            "version": version.version,
            "trigger": trigger_type,
        },
    )
    await session.commit()
    return CreatedRun(run, created=True, dispatch=[item] if item else [])


# ------------------------------------------------------------------ consultas


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> WorkflowRun:
    run = await session.get(WorkflowRun, run_id)
    if run is None:
        raise NotFoundError("La ejecución no existe.")
    return run


async def version_number(session: AsyncSession, version_id: uuid.UUID) -> int:
    number = await session.scalar(
        select(WorkflowVersion.version).where(WorkflowVersion.id == version_id)
    )
    return int(number or 0)


async def run_steps(
    session: AsyncSession, run_id: uuid.UUID
) -> list[tuple[StepRun, list[StepAttempt]]]:
    steps = list(
        await session.scalars(
            select(StepRun).where(StepRun.run_id == run_id).order_by(StepRun.sequence)
        )
    )
    attempts = list(
        await session.scalars(
            select(StepAttempt)
            .where(StepAttempt.step_run_id.in_([s.id for s in steps]))
            .order_by(StepAttempt.attempt_number)
        )
    )
    by_step: dict[uuid.UUID, list[StepAttempt]] = {}
    for attempt in attempts:
        by_step.setdefault(attempt.step_run_id, []).append(attempt)
    return [(step, by_step.get(step.id, [])) for step in steps]


async def list_runs(
    session: AsyncSession,
    *,
    workflow_id: uuid.UUID | None,
    status: RunStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[tuple[WorkflowRun, int]], int]:
    filters = []
    if workflow_id is not None:
        filters.append(WorkflowRun.workflow_id == workflow_id)
    if status is not None:
        filters.append(WorkflowRun.status == status)
    total = await session.scalar(select(func.count()).select_from(WorkflowRun).where(*filters)) or 0
    rows = await session.execute(
        select(WorkflowRun, WorkflowVersion.version)
        .join(WorkflowVersion, WorkflowVersion.id == WorkflowRun.version_id)
        .where(*filters)
        .order_by(WorkflowRun.created_at.desc(), WorkflowRun.id)
        .limit(limit)
        .offset(offset)
    )
    return [(run, number) for run, number in rows.all()], total


# ------------------------------------------------------------------ acciones


async def cancel_run(
    session: AsyncSession, actor_id: uuid.UUID, run_id: uuid.UUID, context: RequestContext
) -> WorkflowRun:
    run = await session.get(WorkflowRun, run_id, with_for_update=True)
    if run is None:
        raise NotFoundError("La ejecución no existe.")
    if run.status in RUN_TERMINAL:
        raise ConflictError(f"La ejecución ya terminó ({run.status.value}).")
    now = utcnow()
    finish_run(run, RunStatus.CANCELLED, now)
    steps = await session.scalars(
        select(StepRun)
        .where(StepRun.run_id == run.id, StepRun.status.not_in(list(STEP_TERMINAL)))
        .with_for_update()
    )
    for step in steps:
        # Un paso en curso no se interrumpe: cuando termine, su resultado se descartará.
        step.status = StepStatus.CANCELLED
        step.finished_at = now
        step.lease_expires_at = None
    record(
        session,
        action=RunAction.CANCELLED,
        context=context,
        actor_user_id=actor_id,
        resource_type="workflow_run",
        resource_id=str(run.id),
    )
    await session.commit()
    return run


async def resolve_step(
    session: AsyncSession,
    settings: Settings,
    *,
    actor_id: uuid.UUID,
    run_id: uuid.UUID,
    step_id: str,
    data: ResolveStep,
    context: RequestContext,
) -> tuple[WorkflowRun, list[DispatchItem]]:
    """Resuelve un paso en revisión manual (resultado desconocido en un paso no idempotente).

    - retry: el operador comprobó que la acción NO se realizó; se reintenta.
    - mark_succeeded: comprobó que SÍ se realizó; se continúa (con la salida que aporte).
    - mark_failed: se da la ejecución por fallida.
    """
    run = await session.get(WorkflowRun, run_id, with_for_update=True)
    if run is None:
        raise NotFoundError("La ejecución no existe.")
    step = await session.scalar(
        select(StepRun)
        .where(StepRun.run_id == run_id, StepRun.step_id == step_id)
        .with_for_update()
    )
    if step is None:
        raise NotFoundError("El paso no existe en esta ejecución.")
    if step.status != StepStatus.NEEDS_REVIEW or run.status != RunStatus.NEEDS_REVIEW:
        raise ConflictError("El paso no está pendiente de revisión.")

    now = utcnow()
    dispatch: list[DispatchItem] = []
    if data.action == "mark_failed":
        assert_step_transition(step.status, StepStatus.FAILED)
        step.status = StepStatus.FAILED
        step.finished_at = now
        finish_run(run, RunStatus.FAILED, now, error={"step_id": step_id, **(step.error or {})})
    else:
        assert_run_transition(run.status, RunStatus.RUNNING)
        run.status = RunStatus.RUNNING
        run.error = None
        if data.action == "retry":
            assert_step_transition(step.status, StepStatus.RETRY_SCHEDULED)
            step.status = StepStatus.RETRY_SCHEDULED
            step.next_attempt_at = now
            # Un reintento manual siempre dispone de al menos un intento más.
            step.max_attempts = max(step.max_attempts, step.attempt_count + 1)
            item = dispatch_item(step, now, settings)
            dispatch = [item] if item else []
        else:
            version = await session.get(WorkflowVersion, run.version_id)
            assert version is not None  # noqa: S101  # FK RESTRICT
            definition = parse_stored(version.definition)
            succeed_step(step, data.output, now)
            definition_step = definition.step_map()[step_id]
            next_id = getattr(definition_step, "next", None)
            following, item = advance(run, step, definition, next_id, data.output, now, settings)
            if following is not None:
                session.add(following)
            dispatch = [item] if item else []

    record(
        session,
        action=RunAction.STEP_RESOLVED,
        context=context,
        actor_user_id=actor_id,
        resource_type="workflow_run",
        resource_id=str(run.id),
        details={"step_id": step_id, "action": data.action, "note": data.note},
    )
    await session.commit()
    return run, dispatch
