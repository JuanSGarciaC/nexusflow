"""Outbox transaccional de webhooks salientes.

Problema: si se notificara un evento después del commit (llamando a Celery o a la URL),
un fallo entre ambos pasos perdería el evento; si se notificara antes, se avisaría de algo
que podría deshacerse con un rollback.

Solución: un hook `before_flush` detecta cuando una ejecución cambia a un estado
notificable e inserta las filas de `webhook_deliveries` en la MISMA transacción. Si la
transacción se confirma, el evento existe; si se deshace, desaparece con ella. Al ser un
hook sobre la sesión, no depende de recordar llamarlo en cada sitio que cambia el estado.

Después del commit, quien hizo el cambio encola las entregas nuevas (`pop_new_deliveries`);
si eso falla, el barrido periódico las recoge.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import any_, event, inspect, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, UOWTransaction

from nexusflow.db.types import utcnow
from nexusflow.engine.dispatch import DELIVER_WEBHOOK_TASK, DispatchItem
from nexusflow.engine.models import RunStatus, WorkflowRun
from nexusflow.webhooks.models import (
    DeliveryStatus,
    WebhookDelivery,
    WebhookEvent,
    WebhookSubscription,
)

DEFAULT_MAX_ATTEMPTS = 8
_NEW_DELIVERIES_KEY = "nexusflow_new_deliveries"

RUN_EVENTS: dict[RunStatus, WebhookEvent] = {
    RunStatus.SUCCEEDED: WebhookEvent.RUN_SUCCEEDED,
    RunStatus.FAILED: WebhookEvent.RUN_FAILED,
    RunStatus.NEEDS_REVIEW: WebhookEvent.RUN_NEEDS_REVIEW,
    RunStatus.TIMED_OUT: WebhookEvent.RUN_TIMED_OUT,
    RunStatus.CANCELLED: WebhookEvent.RUN_CANCELLED,
}


def envelope(event_id: uuid.UUID, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(event_id),
        "type": event_type,
        "created_at": utcnow().isoformat(),
        "data": data,
    }


def run_data(run: WorkflowRun) -> dict[str, Any]:
    return {
        "run_id": str(run.id),
        "workflow_id": str(run.workflow_id),
        "version_id": str(run.version_id),
        "status": run.status.value,
        "trigger_type": run.trigger_type,
        "output": run.output,
        "error": run.error,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def new_delivery(
    subscription_id: uuid.UUID, event_type: str, data: dict[str, Any]
) -> WebhookDelivery:
    event_id = uuid.uuid4()
    return WebhookDelivery(
        id=uuid.uuid4(),
        subscription_id=subscription_id,
        event_id=event_id,
        event_type=event_type,
        payload=envelope(event_id, event_type, data),
        status=DeliveryStatus.PENDING,
        attempt_count=0,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
    )


def _remember(session: Session, deliveries: list[WebhookDelivery]) -> None:
    session.info.setdefault(_NEW_DELIVERIES_KEY, []).extend(d.id for d in deliveries)


@event.listens_for(Session, "before_flush")
def _queue_run_events(session: Session, _ctx: UOWTransaction, _instances: object) -> None:
    changed = [
        obj
        for obj in (*session.new, *session.dirty)
        if isinstance(obj, WorkflowRun) and inspect(obj).attrs.status.history.added
    ]
    for run in changed:
        event_type = RUN_EVENTS.get(run.status)
        if event_type is None:
            continue
        with session.no_autoflush:
            subscriptions = session.scalars(
                select(WebhookSubscription.id).where(
                    WebhookSubscription.is_active.is_(True),
                    literal(event_type.value) == any_(WebhookSubscription.events),
                    or_(
                        WebhookSubscription.workflow_id.is_(None),
                        WebhookSubscription.workflow_id == run.workflow_id,
                    ),
                )
            ).all()
        deliveries = [new_delivery(sid, event_type.value, run_data(run)) for sid in subscriptions]
        session.add_all(deliveries)
        _remember(session, deliveries)


def pop_new_deliveries(session: AsyncSession) -> list[DispatchItem]:
    """Entregas creadas en esta sesión, listas para encolar *después* del commit."""
    ids: list[uuid.UUID] = session.sync_session.info.pop(_NEW_DELIVERIES_KEY, [])
    return [DispatchItem(delivery_id, task=DELIVER_WEBHOOK_TASK) for delivery_id in ids]


def discard_new_deliveries(session: AsyncSession) -> None:
    session.sync_session.info.pop(_NEW_DELIVERIES_KEY, None)


@event.listens_for(Session, "after_rollback")
def _forget_on_rollback(session: Session) -> None:
    # Si la transacción se deshace, las entregas no existen: no hay que encolarlas.
    session.info.pop(_NEW_DELIVERIES_KEY, None)
