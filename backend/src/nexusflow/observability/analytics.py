"""Métricas de ejecución para el panel: volumen, tasa de éxito, tiempos y errores.

Todo se agrega en PostgreSQL (COUNT ... FILTER, percentile_cont, date_trunc): se devuelve
un resumen de tamaño fijo, sin traer filas a Python, sea cual sea el volumen de la ventana.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import Select, case, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexusflow.auth.dependencies import SessionDep, require_permission
from nexusflow.auth.models import User
from nexusflow.auth.permissions import Permission
from nexusflow.db.types import utcnow
from nexusflow.engine.models import RunStatus, StepAttempt, StepRun, WorkflowRun
from nexusflow.workflows.models import Workflow

Bucket = Literal["hour", "day"]
_FAILED = (RunStatus.FAILED, RunStatus.TIMED_OUT)
_TERMINAL = (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.CANCELLED)


# --------------------------------------------------------------------------- esquemas


class DurationStats(BaseModel):
    count: int
    avg_ms: float | None
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None


class TimeBucket(BaseModel):
    start: datetime
    total: int
    succeeded: int
    failed: int


class StepTypeStats(BaseModel):
    step_type: str
    attempts: int
    failed_attempts: int
    p95_ms: float | None


class ErrorClassCount(BaseModel):
    error_class: str
    count: int


class WorkflowFailures(BaseModel):
    workflow_id: uuid.UUID
    name: str
    runs: int
    failed: int


class RecentFailure(BaseModel):
    run_id: uuid.UUID
    workflow_id: uuid.UUID
    workflow_name: str
    status: RunStatus
    error_class: str | None
    message: str | None
    step_id: str | None
    finished_at: datetime | None


class MetricsSummary(BaseModel):
    window_start: datetime
    window_end: datetime
    bucket: Bucket
    workflow_id: uuid.UUID | None
    runs_by_status: dict[str, int]
    total_runs: int
    success_rate: float | None = Field(
        description="Éxitos / ejecuciones terminadas (sin contar canceladas). null si no hay."
    )
    run_duration: DurationStats
    attempts: int
    retries: int = Field(description="Intentos más allá del primero de cada paso")
    timeline: list[TimeBucket]
    step_types: list[StepTypeStats]
    error_classes: list[ErrorClassCount]
    top_failing_workflows: list[WorkflowFailures]
    recent_failures: list[RecentFailure]


# --------------------------------------------------------------------------- consultas


def _epoch_ms(end: Any, start: Any) -> Any:
    return func.extract("epoch", end - start) * 1000


def _scoped[Q: Select[*tuple[Any, ...]]](query: Q, workflow_id: uuid.UUID | None) -> Q:
    return query.where(WorkflowRun.workflow_id == workflow_id) if workflow_id else query


def _fill_timeline(
    rows: dict[datetime, tuple[int, int, int]], start: datetime, end: datetime, bucket: Bucket
) -> list[TimeBucket]:
    """Rellena con ceros los intervalos sin ejecuciones: el gráfico no debe saltarse huecos."""
    step = timedelta(hours=1) if bucket == "hour" else timedelta(days=1)
    cursor = start.replace(minute=0, second=0, microsecond=0)
    if bucket == "day":
        cursor = cursor.replace(hour=0)
    out: list[TimeBucket] = []
    while cursor <= end:
        total, ok, failed = rows.get(cursor, (0, 0, 0))
        out.append(TimeBucket(start=cursor, total=total, succeeded=ok, failed=failed))
        cursor += step
    return out


async def summarize(
    session: AsyncSession,
    *,
    window_hours: int,
    workflow_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> MetricsSummary:
    end = now or utcnow()
    start = end - timedelta(hours=window_hours)
    bucket: Bucket = "hour" if window_hours <= 48 else "day"
    in_window = WorkflowRun.created_at >= start

    # 1. Ejecuciones creadas en la ventana, por estado.
    status_rows = await session.execute(
        _scoped(
            select(WorkflowRun.status, func.count()).where(in_window).group_by(WorkflowRun.status),
            workflow_id,
        )
    )
    by_status = {s.value: 0 for s in RunStatus}
    for status, count in status_rows:
        by_status[status.value] = count
    succeeded = by_status[RunStatus.SUCCEEDED]
    failed = sum(by_status[s] for s in _FAILED)
    success_rate = round(succeeded / (succeeded + failed), 4) if succeeded + failed else None

    # 2. Duración de las ejecuciones terminadas dentro de la ventana.
    duration = _epoch_ms(WorkflowRun.finished_at, WorkflowRun.started_at)
    stats = (
        await session.execute(
            _scoped(
                select(
                    func.count(),
                    func.avg(duration),
                    func.percentile_cont(0.5).within_group(duration),
                    func.percentile_cont(0.95).within_group(duration),
                    func.max(duration),
                ).where(
                    WorkflowRun.finished_at >= start,
                    WorkflowRun.started_at.is_not(None),
                    WorkflowRun.status.in_(_TERMINAL),
                ),
                workflow_id,
            )
        )
    ).one()

    def _ms(value: Any) -> float | None:
        return round(float(value), 1) if value is not None else None

    run_duration = DurationStats(
        count=stats[0],
        avg_ms=_ms(stats[1]),
        p50_ms=_ms(stats[2]),
        p95_ms=_ms(stats[3]),
        max_ms=_ms(stats[4]),
    )

    # 3. Serie temporal por hora o por día.
    # Con zona horaria explícita: el resultado no depende del TimeZone de la sesión.
    bucket_col = func.date_trunc(bucket, WorkflowRun.created_at, "UTC").label("bucket")
    series = await session.execute(
        _scoped(
            select(
                bucket_col,
                func.count(),
                func.count().filter(WorkflowRun.status == RunStatus.SUCCEEDED),
                func.count().filter(WorkflowRun.status.in_(_FAILED)),
            )
            .where(in_window)
            .group_by(literal_column("bucket"))
            .order_by(literal_column("bucket")),
            workflow_id,
        )
    )
    timeline = _fill_timeline(
        {row[0]: (row[1], row[2], row[3]) for row in series}, start, end, bucket
    )

    # 4. Intentos de pasos: reintentos, tiempos por tipo y clases de error.
    # Sin filtro por flujo no hace falta ningún JOIN (step_type está en step_attempts).
    attempts_base = select().select_from(StepAttempt).where(StepAttempt.started_at >= start)
    if workflow_id:
        attempts_base = (
            attempts_base.join(StepRun, StepRun.id == StepAttempt.step_run_id)
            .join(WorkflowRun, WorkflowRun.id == StepRun.run_id)
            .where(WorkflowRun.workflow_id == workflow_id)
        )
    totals = (
        await session.execute(
            attempts_base.add_columns(
                func.count(), func.count().filter(StepAttempt.attempt_number > 1)
            )
        )
    ).one()

    per_type = await session.execute(
        attempts_base.add_columns(
            StepAttempt.step_type,
            func.count(),
            func.count().filter(StepAttempt.outcome == "failed"),
            func.percentile_cont(0.95).within_group(StepAttempt.duration_ms),
        )
        .group_by(StepAttempt.step_type)
        .order_by(StepAttempt.step_type)
    )
    step_types = [
        StepTypeStats(step_type=t, attempts=n, failed_attempts=f, p95_ms=_ms(p95))
        for t, n, f, p95 in per_type
    ]

    errors = await session.execute(
        attempts_base.add_columns(StepAttempt.error_class, func.count())
        .where(StepAttempt.error_class.is_not(None))
        .group_by(StepAttempt.error_class)
        .order_by(func.count().desc(), StepAttempt.error_class)
        .limit(10)
    )
    error_classes = [ErrorClassCount(error_class=c, count=n) for c, n in errors]

    # 5. Flujos con más fallos en la ventana.
    failed_count = func.count().filter(WorkflowRun.status.in_(_FAILED))
    top = await session.execute(
        _scoped(
            select(Workflow.id, Workflow.name, func.count(), failed_count)
            .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
            .where(in_window)
            .group_by(Workflow.id, Workflow.name)
            .having(failed_count > 0)
            .order_by(failed_count.desc(), Workflow.name)
            .limit(5),
            workflow_id,
        )
    )
    top_failing = [WorkflowFailures(workflow_id=i, name=n, runs=r, failed=f) for i, n, r, f in top]

    # 6. Últimos fallos, con el error resumido para mostrarlo en el panel.
    attention = (*_FAILED, RunStatus.NEEDS_REVIEW)
    recent = await session.execute(
        _scoped(
            select(WorkflowRun, Workflow.name)
            .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
            .where(WorkflowRun.status.in_(attention), in_window)
            .order_by(
                case(
                    (WorkflowRun.finished_at.is_(None), WorkflowRun.created_at),
                    else_=WorkflowRun.finished_at,
                ).desc()
            )
            .limit(10),
            workflow_id,
        )
    )
    recent_failures = []
    for run, name in recent:
        error = run.error or {}
        recent_failures.append(
            RecentFailure(
                run_id=run.id,
                workflow_id=run.workflow_id,
                workflow_name=name,
                status=run.status,
                error_class=error.get("class"),
                message=error.get("message"),
                step_id=error.get("step_id"),
                finished_at=run.finished_at,
            )
        )

    return MetricsSummary(
        window_start=start,
        window_end=end,
        bucket=bucket,
        workflow_id=workflow_id,
        runs_by_status=by_status,
        total_runs=sum(by_status.values()),
        success_rate=success_rate,
        run_duration=run_duration,
        attempts=totals[0],
        retries=totals[1],
        timeline=timeline,
        step_types=step_types,
        error_classes=error_classes,
        top_failing_workflows=top_failing,
        recent_failures=recent_failures,
    )


# --------------------------------------------------------------------------- endpoint

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])
Reader = Annotated[User, Depends(require_permission(Permission.METRICS_READ))]


@router.get(
    "/summary",
    response_model=MetricsSummary,
    summary="Resumen de ejecuciones: volumen, tasa de éxito, tiempos y errores",
)
async def metrics_summary(
    session: SessionDep,
    _: Reader,
    window_hours: Annotated[int, Query(ge=1, le=24 * 30, description="Ventana en horas")] = 24,
    workflow_id: Annotated[uuid.UUID | None, Query(description="Limitar a un flujo")] = None,
) -> MetricsSummary:
    return await summarize(session, window_hours=window_hours, workflow_id=workflow_id)


__all__ = ["MetricsSummary", "router", "summarize"]
