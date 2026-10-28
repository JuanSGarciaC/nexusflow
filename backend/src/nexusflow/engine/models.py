"""Ejecuciones, pasos e intentos, con sus máquinas de estado.

PostgreSQL es la fuente de verdad (ADR-0002): un mensaje de Celery solo dice "revisa el
paso X". Las transiciones se aplican con UPDATE condicionales (`WHERE status IN (...)`), de
modo que dos workers que reciben el mismo mensaje no pueden ejecutar el mismo intento.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexusflow.db.base import Base
from nexusflow.db.types import UUIDPrimaryKeyMixin


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda items: [i.value for i in items],
    )


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


RUN_TERMINAL = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.TIMED_OUT}
)
RUN_ACTIVE = frozenset({RunStatus.PENDING, RunStatus.RUNNING})


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_SCHEDULED = "retry_scheduled"
    NEEDS_REVIEW = "needs_review"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


STEP_CLAIMABLE = frozenset({StepStatus.PENDING, StepStatus.RETRY_SCHEDULED})
STEP_TERMINAL = frozenset({StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.CANCELLED})

# Transiciones permitidas. Se comprueban en código (assert_transition) y, además, cada
# UPDATE del motor filtra por el estado de origen.
RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset(
        {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED, RunStatus.TIMED_OUT}
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.NEEDS_REVIEW,
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
        }
    ),
    RunStatus.NEEDS_REVIEW: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
}
STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset({StepStatus.RUNNING, StepStatus.CANCELLED}),
    StepStatus.RETRY_SCHEDULED: frozenset({StepStatus.RUNNING, StepStatus.CANCELLED}),
    StepStatus.RUNNING: frozenset(
        {
            StepStatus.SUCCEEDED,
            StepStatus.FAILED,
            StepStatus.RETRY_SCHEDULED,
            StepStatus.NEEDS_REVIEW,
            StepStatus.CANCELLED,
        }
    ),
    StepStatus.NEEDS_REVIEW: frozenset(
        {StepStatus.RETRY_SCHEDULED, StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.CANCELLED}
    ),
}


class InvalidTransitionError(RuntimeError):
    pass


def assert_run_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in RUN_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransitionError(f"Ejecución: transición no permitida {current} → {target}")


def assert_step_transition(current: StepStatus, target: StepStatus) -> None:
    if target not in STEP_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransitionError(f"Paso: transición no permitida {current} → {target}")


class WorkflowRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        # Idempotencia de la creación: la misma clave devuelve la misma ejecución.
        Index(
            "uq_workflow_runs_idempotency",
            "workflow_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_workflow_runs_workflow_created", "workflow_id", "created_at"),
        Index("ix_workflow_runs_status", "status"),
        # Métricas e historial global por ventana de tiempo (sin filtrar por flujo).
        Index("ix_workflow_runs_created_at", "created_at"),
        Index("ix_workflow_runs_finished_at", "finished_at"),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="RESTRICT"), nullable=False
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        _enum(RunStatus, "run_status"), nullable=False, default=RunStatus.PENDING
    )
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[Any | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StepRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "step_runs"
    __table_args__ = (
        UniqueConstraint("run_id", "step_id", name="uq_step_runs_run_step"),
        # Lo que barre el dispatcher: pasos listos o con el plazo de trabajo vencido.
        Index(
            "ix_step_runs_dispatch",
            "status",
            "next_attempt_at",
            postgresql_where=text("status IN ('pending', 'retry_scheduled', 'running')"),
        ),
        CheckConstraint("attempt_count >= 0", name="attempts_non_negative"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str] = mapped_column(String(63), nullable=False)
    step_type: Mapped[str] = mapped_column(String(20), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[StepStatus] = mapped_column(
        _enum(StepStatus, "step_status"), nullable=False, default=StepStatus.PENDING
    )
    side_effect: Mapped[str] = mapped_column(String(20), nullable=False)
    # Estable entre reintentos: el sistema externo puede deduplicar con ella.
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    output: Mapped[Any | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Mientras un worker ejecuta el paso; si vence, el barrido asume que el worker murió.
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Evita que el barrido reencole una y otra vez un paso que ya está en la cola.
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StepAttempt(Base):
    __tablename__ = "step_attempts"
    __table_args__ = (
        UniqueConstraint("step_run_id", "attempt_number", name="uq_step_attempts_number"),
        Index("ix_step_attempts_started_at", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    step_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("step_runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str | None] = mapped_column(String(20))  # succeeded | failed | abandoned
    error_class: Mapped[str | None] = mapped_column(String(40))
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(String(1000))
