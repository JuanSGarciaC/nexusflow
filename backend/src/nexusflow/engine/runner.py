"""Motor de ejecución (lado worker).

Ciclo de un paso, en tres transacciones cortas:

1. **Reclamar**: bloquea la ejecución y el paso, comprueba que el paso se puede ejecutar
   (pendiente o reintento programado, y ya vencido) y lo marca `running` con un *lease*.
   Si llega un mensaje duplicado, el segundo ve el paso en `running` y no hace nada.
2. **Ejecutar** fuera de la transacción (la llamada HTTP puede tardar).
3. **Registrar**: vuelve a bloquear, comprueba que el reclamo sigue vigente (el barrido
   pudo darlo por perdido) y aplica éxito, reintento, revisión manual o fallo.

Orden de bloqueo siempre ejecución → paso, también en la API: así no hay interbloqueos.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexusflow.core.config import Settings
from nexusflow.core.logging import get_logger
from nexusflow.db.types import utcnow
from nexusflow.engine.dispatch import Dispatcher, DispatchItem
from nexusflow.engine.executors import (
    ExecutionContext,
    ExecutorDeps,
    StepError,
    StepSuccess,
    execute_step,
)
from nexusflow.engine.http_client import FailureKind
from nexusflow.engine.models import (
    RUN_ACTIVE,
    STEP_CLAIMABLE,
    STEP_TERMINAL,
    RunStatus,
    StepAttempt,
    StepRun,
    StepStatus,
    WorkflowRun,
    assert_run_transition,
    assert_step_transition,
)
from nexusflow.engine.planning import (
    advance,
    compute_backoff,
    dispatch_item,
    finish_run,
    succeed_step,
)
from nexusflow.webhooks.outbox import pop_new_deliveries
from nexusflow.workflows.dsl import SideEffect, WorkflowDefinition
from nexusflow.workflows.models import WorkflowVersion
from nexusflow.workflows.service import parse_stored

logger = get_logger(__name__)

# Si un paso pendiente ya se encoló hace menos de esto, el barrido no lo vuelve a encolar.
REDISPATCH_AFTER = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class Claim:
    run_id: uuid.UUID
    step_run_id: uuid.UUID
    step_id: str
    attempt: int
    started_at: datetime


@dataclass(slots=True)
class SweepReport:
    dispatched: int = 0
    recovered: int = 0
    timed_out: int = 0
    errors: list[str] = field(default_factory=list)


async def load_definition(session: AsyncSession, run: WorkflowRun) -> WorkflowDefinition:
    version = await session.get(WorkflowVersion, run.version_id)
    if version is None:  # imposible por la FK RESTRICT, pero se comprueba
        raise RuntimeError(f"La versión {run.version_id} no existe")
    return parse_stored(version.definition)


async def _lock_run_and_step(
    session: AsyncSession, run_id: uuid.UUID, step_run_id: uuid.UUID
) -> tuple[WorkflowRun, StepRun]:
    run = await session.get(WorkflowRun, run_id, with_for_update=True, populate_existing=True)
    step = await session.get(StepRun, step_run_id, with_for_update=True, populate_existing=True)
    if run is None or step is None:
        raise RuntimeError("La ejecución o el paso desaparecieron")
    return run, step


async def _attempt(
    session: AsyncSession, step_run_id: uuid.UUID, number: int
) -> StepAttempt | None:
    attempt: StepAttempt | None = await session.scalar(
        select(StepAttempt).where(
            StepAttempt.step_run_id == step_run_id, StepAttempt.attempt_number == number
        )
    )
    return attempt


def _close_attempt(
    attempt: StepAttempt | None,
    now: datetime,
    outcome: str,
    error: StepError | None = None,
) -> None:
    if attempt is None or attempt.finished_at is not None:
        return
    attempt.finished_at = now
    attempt.duration_ms = int((now - attempt.started_at).total_seconds() * 1000)
    attempt.outcome = outcome
    if error is not None:
        attempt.error_class = error.error_class[:40]
        attempt.http_status = error.http_status
        attempt.error_message = error.message[:1000]


def apply_failure(
    run: WorkflowRun,
    step: StepRun,
    definition: WorkflowDefinition,
    error: StepError,
    now: datetime,
    settings: Settings,
) -> tuple[str, DispatchItem | None]:
    """Decide qué hacer con un fallo. Devuelve una etiqueta y lo que haya que encolar.

    La regla clave: un resultado DESCONOCIDO en un paso NO IDEMPOTENTE nunca se reintenta
    solo, porque podría duplicar el efecto (un cobro, un pedido). Se para para revisión.
    """
    step.error = error.as_dict()
    step.lease_expires_at = None
    side_effect = SideEffect(step.side_effect)
    unsafe = error.kind == FailureKind.UNKNOWN and side_effect == SideEffect.NON_IDEMPOTENT
    retryable = error.kind in (FailureKind.NOT_SENT, FailureKind.REJECTED) or (
        error.kind == FailureKind.UNKNOWN and not unsafe
    )

    if unsafe:
        assert_step_transition(step.status, StepStatus.NEEDS_REVIEW)
        step.status = StepStatus.NEEDS_REVIEW
        if run.status == RunStatus.RUNNING:
            assert_run_transition(run.status, RunStatus.NEEDS_REVIEW)
            run.status = RunStatus.NEEDS_REVIEW
            run.error = {"step_id": step.step_id, **error.as_dict()}
        return "needs_review", None

    if retryable and step.attempt_count < step.max_attempts and run.status == RunStatus.RUNNING:
        policy = definition.step_map()[step.step_id].retry
        delay = compute_backoff(policy, step.attempt_count, error.retry_after_seconds)
        assert_step_transition(step.status, StepStatus.RETRY_SCHEDULED)
        step.status = StepStatus.RETRY_SCHEDULED
        step.next_attempt_at = now + timedelta(seconds=delay)
        return "retry_scheduled", dispatch_item(step, now, settings)

    assert_step_transition(step.status, StepStatus.FAILED)
    step.status = StepStatus.FAILED
    step.finished_at = now
    if run.status == RunStatus.RUNNING:
        finish_run(run, RunStatus.FAILED, now, error={"step_id": step.step_id, **error.as_dict()})
    return "failed", None


class Engine:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        dispatcher: Dispatcher,
        executor_deps: ExecutorDeps,
    ) -> None:
        self._sessions = session_factory
        self._settings = settings
        self._dispatcher = dispatcher
        self._deps = executor_deps

    # ------------------------------------------------------------------ un paso

    async def process_step(self, step_run_id: uuid.UUID) -> str:
        claim = await self._claim(step_run_id)
        if claim is None:
            return "skipped"
        log = logger.bind(run_id=str(claim.run_id), step_id=claim.step_id, attempt=claim.attempt)

        try:
            step, context = await self._prepare(claim)
            success = await asyncio.wait_for(
                execute_step(step, context, self._deps), timeout=step.timeout_seconds + 5
            )
        except StepError as error:
            log.info("step_failed", error_class=error.error_class, kind=error.kind.value)
            return await self._record_failure(claim, error)
        except TimeoutError:
            return await self._record_failure(
                claim, StepError(FailureKind.UNKNOWN, "timeout", "El paso superó su tiempo límite")
            )
        except Exception:
            # Fallo inesperado del propio motor: se trata como resultado desconocido, la
            # opción conservadora (un paso no idempotente quedará en revisión).
            log.exception("step_internal_error")
            return await self._record_failure(
                claim, StepError(FailureKind.UNKNOWN, "internal_error", "Error interno del motor")
            )
        log.info("step_succeeded")
        return await self._record_success(claim, success)

    async def _claim(self, step_run_id: uuid.UUID) -> Claim | None:
        async with self._sessions() as session:
            found = await session.get(StepRun, step_run_id)
            if found is None or found.status not in STEP_CLAIMABLE:
                return None
            run, step = await _lock_run_and_step(session, found.run_id, step_run_id)
            now = utcnow()
            if step.status not in STEP_CLAIMABLE or run.status not in RUN_ACTIVE:
                return None  # otro worker lo tomó, o la ejecución ya no está activa
            if step.next_attempt_at is not None and step.next_attempt_at > now:
                return None  # mensaje adelantado: el barrido lo encolará a su hora
            if run.deadline_at <= now:
                await self._time_out(session, run, now)
                await session.commit()
                await self._dispatcher.dispatch(pop_new_deliveries(session))
                return None

            if run.status == RunStatus.PENDING:
                assert_run_transition(run.status, RunStatus.RUNNING)
                run.status = RunStatus.RUNNING
                run.started_at = now
            assert_step_transition(step.status, StepStatus.RUNNING)
            step.status = StepStatus.RUNNING
            step.attempt_count += 1
            step.started_at = step.started_at or now
            step.lease_expires_at = now + timedelta(
                seconds=step.timeout_seconds + self._settings.step_lease_grace_seconds
            )
            session.add(
                StepAttempt(
                    step_run_id=step.id,
                    attempt_number=step.attempt_count,
                    started_at=now,
                    step_type=step.step_type,
                )
            )
            claim = Claim(run.id, step.id, step.step_id, step.attempt_count, now)
            await session.commit()
            return claim

    async def _prepare(self, claim: Claim) -> tuple[Any, ExecutionContext]:
        async with self._sessions() as session:
            run = await session.get(WorkflowRun, claim.run_id)
            assert run is not None  # noqa: S101  # recién reclamado
            definition = await load_definition(session, run)
            previous = await session.execute(
                select(StepRun.step_id, StepRun.output).where(
                    StepRun.run_id == run.id, StepRun.status == StepStatus.SUCCEEDED
                )
            )
            step_run = await session.get(StepRun, claim.step_run_id)
            assert step_run is not None  # noqa: S101
            context = ExecutionContext(
                run_id=str(run.id),
                workflow_id=str(run.workflow_id),
                input=run.input,
                outputs={sid: out for sid, out in previous.all()},
                idempotency_key=step_run.idempotency_key,
            )
            return definition.step_map()[claim.step_id], context

    async def _is_stale(self, step: StepRun, claim: Claim) -> bool:
        return step.status != StepStatus.RUNNING or step.attempt_count != claim.attempt

    async def _record_success(self, claim: Claim, success: StepSuccess) -> str:
        to_dispatch: list[DispatchItem] = []
        async with self._sessions() as session:
            run, step = await _lock_run_and_step(session, claim.run_id, claim.step_run_id)
            now = utcnow()
            attempt = await _attempt(session, step.id, claim.attempt)
            if await self._is_stale(step, claim):
                # El barrido lo dio por perdido (o se canceló) mientras se ejecutaba.
                _close_attempt(attempt, now, "stale")
                await session.commit()
                logger.warning("stale_step_result_discarded", step_id=claim.step_id)
                return "stale"
            _close_attempt(attempt, now, "succeeded")
            succeed_step(step, success.output, now)
            definition = await load_definition(session, run)
            following, item = advance(
                run, step, definition, success.next_step, success.output, now, self._settings
            )
            if following is not None:
                session.add(following)
            if item is not None:
                to_dispatch.append(item)
            await session.commit()
            to_dispatch.extend(pop_new_deliveries(session))
        await self._dispatcher.dispatch(to_dispatch)
        return "succeeded"

    async def _record_failure(self, claim: Claim, error: StepError) -> str:
        to_dispatch: list[DispatchItem] = []
        async with self._sessions() as session:
            run, step = await _lock_run_and_step(session, claim.run_id, claim.step_run_id)
            now = utcnow()
            attempt = await _attempt(session, step.id, claim.attempt)
            if await self._is_stale(step, claim):
                _close_attempt(attempt, now, "stale", error)
                await session.commit()
                return "stale"
            _close_attempt(attempt, now, "failed", error)
            definition = await load_definition(session, run)
            label, item = apply_failure(run, step, definition, error, now, self._settings)
            if item is not None:
                to_dispatch.append(item)
            await session.commit()
            to_dispatch.extend(pop_new_deliveries(session))
        await self._dispatcher.dispatch(to_dispatch)
        return label

    # ------------------------------------------------------------------ barrido periódico

    async def _time_out(self, session: AsyncSession, run: WorkflowRun, now: datetime) -> None:
        finish_run(
            run,
            RunStatus.TIMED_OUT,
            now,
            error={"class": "run_timeout", "message": "La ejecución superó su duración máxima"},
        )
        steps = await session.scalars(
            select(StepRun)
            .where(
                StepRun.run_id == run.id,
                StepRun.status.not_in(list(STEP_TERMINAL)),
            )
            .with_for_update()
        )
        for step in steps:
            step.status = StepStatus.CANCELLED
            step.finished_at = now
            step.lease_expires_at = None

    async def sweep(self, *, limit: int = 200) -> SweepReport:
        """Recupera lo que los mensajes no garantizan.

        1. Ejecuciones activas pasadas de su plazo → timed_out.
        2. Pasos en `running` con el lease vencido (el worker murió) → se tratan como un
           fallo de resultado desconocido: reintento si es seguro, si no, revisión manual.
        3. Pasos listos que nadie está procesando (mensaje perdido, Redis caído al encolar,
           esperas largas) → se encolan de nuevo.
        """
        report = SweepReport()
        now = utcnow()
        async with self._sessions() as session:
            expired_runs = list(
                await session.scalars(
                    select(WorkflowRun.id)
                    .where(
                        WorkflowRun.status.in_(
                            [RunStatus.PENDING, RunStatus.RUNNING, RunStatus.NEEDS_REVIEW]
                        ),
                        WorkflowRun.deadline_at <= now,
                    )
                    .limit(limit)
                )
            )
            lost_steps = list(
                (
                    await session.execute(
                        select(StepRun.run_id, StepRun.id)
                        .join(WorkflowRun, WorkflowRun.id == StepRun.run_id)
                        .where(
                            StepRun.status == StepStatus.RUNNING,
                            StepRun.lease_expires_at <= now,
                            WorkflowRun.status == RunStatus.RUNNING,
                        )
                        .limit(limit)
                    )
                ).all()
            )
            due = list(
                await session.scalars(
                    select(StepRun.id)
                    .join(WorkflowRun, WorkflowRun.id == StepRun.run_id)
                    .where(
                        StepRun.status.in_([StepStatus.PENDING, StepStatus.RETRY_SCHEDULED]),
                        or_(StepRun.next_attempt_at.is_(None), StepRun.next_attempt_at <= now),
                        or_(
                            StepRun.last_dispatched_at.is_(None),
                            StepRun.last_dispatched_at <= now - REDISPATCH_AFTER,
                        ),
                        WorkflowRun.status.in_(list(RUN_ACTIVE)),
                    )
                    .order_by(StepRun.next_attempt_at.nulls_first())
                    .limit(limit)
                )
            )

        for run_id in expired_runs:
            async with self._sessions() as session:
                run = await session.get(WorkflowRun, run_id, with_for_update=True)
                if run is not None and run.status in (
                    RunStatus.PENDING,
                    RunStatus.RUNNING,
                    RunStatus.NEEDS_REVIEW,
                ):
                    await self._time_out(session, run, utcnow())
                    report.timed_out += 1
                await session.commit()
                await self._dispatcher.dispatch(pop_new_deliveries(session))

        for run_id, step_run_id in lost_steps:
            if await self._recover_lost(run_id, step_run_id):
                report.recovered += 1

        items: list[DispatchItem] = []
        if due:
            async with self._sessions() as session:
                stamp = utcnow()
                rows = await session.scalars(
                    select(StepRun).where(StepRun.id.in_(due)).with_for_update(skip_locked=True)
                )
                for step in rows:
                    if step.status in STEP_CLAIMABLE:
                        step.last_dispatched_at = stamp
                        items.append(DispatchItem(step.id))
                await session.commit()
        await self._dispatcher.dispatch(items)
        report.dispatched = len(items)
        if report.dispatched or report.recovered or report.timed_out:
            logger.info(
                "sweep_completed",
                dispatched=report.dispatched,
                recovered=report.recovered,
                timed_out=report.timed_out,
            )
        return report

    async def _recover_lost(self, run_id: uuid.UUID, step_run_id: uuid.UUID) -> bool:
        to_dispatch: list[DispatchItem] = []
        async with self._sessions() as session:
            run, step = await _lock_run_and_step(session, run_id, step_run_id)
            now = utcnow()
            if (
                step.status != StepStatus.RUNNING
                or step.lease_expires_at is None
                or step.lease_expires_at > now
                or run.status != RunStatus.RUNNING
            ):
                return False  # terminó justo a tiempo
            attempt = await _attempt(session, step.id, step.attempt_count)
            error = StepError(
                FailureKind.UNKNOWN,
                "worker_lost",
                "El worker dejó de responder durante el paso; su resultado es desconocido",
            )
            _close_attempt(attempt, now, "abandoned", error)
            definition = await load_definition(session, run)
            label, item = apply_failure(run, step, definition, error, now, self._settings)
            if item is not None:
                to_dispatch.append(item)
            await session.commit()
            to_dispatch.extend(pop_new_deliveries(session))
            logger.warning("lost_step_recovered", step_id=step.step_id, outcome=label)
        await self._dispatcher.dispatch(to_dispatch)
        return True
