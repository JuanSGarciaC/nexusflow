"""Reglas compartidas por la API y el worker para avanzar una ejecución.

Todas las funciones operan sobre objetos ya bloqueados en la sesión del llamador y
devuelven lo que hay que encolar *después* del commit.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta
from typing import Any

from nexusflow.core.config import Settings
from nexusflow.engine.dispatch import DispatchItem
from nexusflow.engine.models import (
    RunStatus,
    StepRun,
    StepStatus,
    WorkflowRun,
    assert_run_transition,
    assert_step_transition,
)
from nexusflow.workflows.dsl import (
    DelayStep,
    RetryPolicy,
    Step,
    WorkflowDefinition,
    step_side_effect,
)


def new_step_run(run: WorkflowRun, step: Step, sequence: int, now: datetime) -> StepRun:
    next_attempt_at = (
        now + timedelta(seconds=step.config.seconds) if isinstance(step, DelayStep) else None
    )
    return StepRun(
        id=uuid.uuid4(),  # explícito: se necesita antes del flush para encolarlo
        run_id=run.id,
        step_id=step.id,
        step_type=step.type,
        sequence=sequence,
        status=StepStatus.PENDING,
        side_effect=step_side_effect(step).value,
        idempotency_key=f"{run.id}:{step.id}",
        attempt_count=0,
        max_attempts=step.retry.max_attempts,
        timeout_seconds=step.timeout_seconds,
        next_attempt_at=next_attempt_at,
    )


def dispatch_item(step_run: StepRun, now: datetime, settings: Settings) -> DispatchItem | None:
    """Encola ya si está listo; con countdown si la espera es corta; si no, lo hará el barrido."""
    if step_run.next_attempt_at is None or step_run.next_attempt_at <= now:
        step_run.last_dispatched_at = now
        return DispatchItem(step_run.id)
    wait = (step_run.next_attempt_at - now).total_seconds()
    if wait <= settings.max_dispatch_countdown_seconds:
        step_run.last_dispatched_at = now
        return DispatchItem(step_run.id, countdown=wait)
    return None


def compute_backoff(
    policy: RetryPolicy,
    attempt: int,
    retry_after: float | None = None,
    rng: random.Random | None = None,
) -> float:
    """Backoff exponencial con jitter ("equal jitter").

    La mitad del intervalo es fija y la otra mitad aleatoria: así los clientes que fallaron
    a la vez no reintentan todos en el mismo instante (efecto manada). Si el servidor envía
    Retry-After, se respeta como mínimo.
    """
    base = min(
        policy.max_backoff_seconds,
        policy.initial_backoff_seconds * policy.backoff_multiplier ** max(attempt - 1, 0),
    )
    jitter = (rng or random).uniform(0, base / 2)  # no es criptográfico
    delay = base / 2 + jitter
    if retry_after is not None:
        delay = max(delay, min(retry_after, 3600.0))
    return round(delay, 3)


def finish_run(
    run: WorkflowRun,
    status: RunStatus,
    now: datetime,
    *,
    output: Any = None,
    error: dict[str, Any] | None = None,
) -> None:
    assert_run_transition(run.status, status)
    run.status = status
    run.finished_at = now
    if output is not None:
        run.output = output
    if error is not None:
        run.error = error


def succeed_step(step_run: StepRun, output: Any, now: datetime) -> None:
    assert_step_transition(step_run.status, StepStatus.SUCCEEDED)
    step_run.status = StepStatus.SUCCEEDED
    step_run.output = output
    step_run.error = None
    step_run.finished_at = now
    step_run.lease_expires_at = None
    step_run.next_attempt_at = None


def advance(
    run: WorkflowRun,
    step_run: StepRun,
    definition: WorkflowDefinition,
    next_step_id: str | None,
    output: Any,
    now: datetime,
    settings: Settings,
) -> tuple[StepRun | None, DispatchItem | None]:
    """Tras un paso con éxito: crea el siguiente (y lo que hay que encolar) o termina."""
    if run.status != RunStatus.RUNNING:
        return None, None  # cancelada o vencida mientras el paso se ejecutaba
    if next_step_id is None:
        finish_run(run, RunStatus.SUCCEEDED, now, output=output)
        return None, None
    step = definition.step_map()[next_step_id]
    following = new_step_run(run, step, step_run.sequence + 1, now)
    return following, dispatch_item(following, now, settings)
