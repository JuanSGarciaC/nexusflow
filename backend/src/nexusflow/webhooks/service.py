"""Casos de uso de webhooks entrantes y salientes."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexusflow.audit.service import RequestContext, record
from nexusflow.auth.models import User
from nexusflow.core.config import Settings
from nexusflow.core.errors import AppError, ConflictError, NotFoundError, UnauthorizedError
from nexusflow.core.logging import get_logger
from nexusflow.db.types import utcnow
from nexusflow.engine import service as runs
from nexusflow.engine.dispatch import DELIVER_WEBHOOK_TASK, DispatchItem
from nexusflow.engine.schemas import RunCreate
from nexusflow.integrations.crypto import CredentialCipher, CredentialDecryptionError
from nexusflow.webhooks import signing
from nexusflow.webhooks.delivery import (
    SUBSCRIPTION_SECRET_SCOPE,
    decrypt_secret,
    encrypt_secret,
)
from nexusflow.webhooks.models import (
    DeliveryStatus,
    WebhookDelivery,
    WebhookEvent,
    WebhookSubscription,
    WebhookTrigger,
)
from nexusflow.webhooks.outbox import new_delivery
from nexusflow.webhooks.schemas import (
    SubscriptionCreate,
    SubscriptionUpdate,
    TriggerCreate,
    TriggerUpdate,
)
from nexusflow.workflows.models import Workflow, WorkflowStatus

logger = get_logger(__name__)

TRIGGER_SECRET_SCOPE = "webhook-trigger"  # noqa: S105  # ámbito, no un secreto
_EVENT_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,80}$")


class WebhookAction:
    TRIGGER_CREATED = "webhook.trigger_created"
    TRIGGER_UPDATED = "webhook.trigger_updated"
    TRIGGER_SECRET_ROTATED = "webhook.trigger_secret_rotated"  # noqa: S105
    TRIGGER_DELETED = "webhook.trigger_deleted"
    SUBSCRIPTION_CREATED = "webhook.subscription_created"
    SUBSCRIPTION_UPDATED = "webhook.subscription_updated"
    SUBSCRIPTION_SECRET_ROTATED = "webhook.subscription_secret_rotated"  # noqa: S105
    SUBSCRIPTION_DELETED = "webhook.subscription_deleted"
    DELIVERY_REQUEUED = "webhook.delivery_requeued"


class InvalidPayloadError(AppError):
    status_code = 422
    code = "invalid-webhook-payload"
    title = "El cuerpo del webhook no es válido"


def _audit(
    session: AsyncSession,
    action: str,
    actor: User,
    context: RequestContext,
    resource_type: str,
    resource_id: uuid.UUID,
    details: dict[str, Any] | None = None,
) -> None:
    record(
        session,
        action=action,
        context=context,
        actor_user_id=actor.id,
        resource_type=resource_type,
        resource_id=str(resource_id),
        details=details,
    )


def check_target_url(url: str, settings: Settings) -> str:
    """Validación temprana de la URL de una suscripción. La comprobación anti-SSRF
    definitiva (DNS → IP pública) se hace en cada entrega, porque el DNS puede cambiar."""
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, TypeError) as exc:
        raise InvalidPayloadError("La URL no es válida.") from exc
    if parsed.scheme != "https" and not (parsed.scheme == "http" and settings.egress_allow_http):
        raise InvalidPayloadError("La URL debe usar https.")
    if parsed.userinfo or not parsed.host:
        raise InvalidPayloadError("La URL no puede llevar credenciales y debe tener host.")
    return url


# ------------------------------------------------------------------ triggers (entrantes)


async def _active_workflow(session: AsyncSession, workflow_id: uuid.UUID) -> Workflow:
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise NotFoundError("El flujo no existe.")
    return workflow


async def create_trigger(
    session: AsyncSession,
    cipher: CredentialCipher,
    actor: User,
    workflow_id: uuid.UUID,
    data: TriggerCreate,
    context: RequestContext,
) -> tuple[WebhookTrigger, str]:
    await _active_workflow(session, workflow_id)
    trigger_id = uuid.uuid4()
    secret = signing.generate_secret()
    trigger = WebhookTrigger(
        id=trigger_id,
        workflow_id=workflow_id,
        name=data.name,
        rate_limit_per_minute=data.rate_limit_per_minute,
        secret_encrypted=encrypt_secret(cipher, trigger_id, TRIGGER_SECRET_SCOPE, secret),
        created_by=actor.id,
    )
    session.add(trigger)
    _audit(session, WebhookAction.TRIGGER_CREATED, actor, context, "webhook_trigger", trigger_id)
    await session.commit()
    return trigger, secret


async def list_triggers(session: AsyncSession, workflow_id: uuid.UUID) -> list[WebhookTrigger]:
    await _active_workflow(session, workflow_id)
    rows = await session.scalars(
        select(WebhookTrigger)
        .where(WebhookTrigger.workflow_id == workflow_id)
        .order_by(WebhookTrigger.created_at)
    )
    return list(rows)


async def _trigger(session: AsyncSession, trigger_id: uuid.UUID) -> WebhookTrigger:
    trigger = await session.get(WebhookTrigger, trigger_id, with_for_update=True)
    if trigger is None:
        raise NotFoundError("El trigger no existe.")
    return trigger


async def update_trigger(
    session: AsyncSession,
    actor: User,
    trigger_id: uuid.UUID,
    data: TriggerUpdate,
    context: RequestContext,
) -> WebhookTrigger:
    trigger = await _trigger(session, trigger_id)
    changes = data.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(trigger, key, value)
    _audit(
        session,
        WebhookAction.TRIGGER_UPDATED,
        actor,
        context,
        "webhook_trigger",
        trigger_id,
        changes,
    )
    await session.commit()
    await session.refresh(trigger)
    return trigger


async def rotate_trigger_secret(
    session: AsyncSession,
    cipher: CredentialCipher,
    actor: User,
    trigger_id: uuid.UUID,
    context: RequestContext,
) -> tuple[WebhookTrigger, str]:
    trigger = await _trigger(session, trigger_id)
    secret = signing.generate_secret()
    trigger.secret_encrypted = encrypt_secret(cipher, trigger.id, TRIGGER_SECRET_SCOPE, secret)
    _audit(
        session, WebhookAction.TRIGGER_SECRET_ROTATED, actor, context, "webhook_trigger", trigger_id
    )
    await session.commit()
    await session.refresh(trigger)
    return trigger, secret


async def delete_trigger(
    session: AsyncSession, actor: User, trigger_id: uuid.UUID, context: RequestContext
) -> None:
    trigger = await _trigger(session, trigger_id)
    await session.delete(trigger)
    _audit(session, WebhookAction.TRIGGER_DELETED, actor, context, "webhook_trigger", trigger_id)
    await session.commit()


async def find_active_trigger(session: AsyncSession, trigger_id: uuid.UUID) -> WebhookTrigger:
    trigger = await session.get(WebhookTrigger, trigger_id)
    workflow = await session.get(Workflow, trigger.workflow_id) if trigger else None
    if (
        trigger is None
        or not trigger.is_active
        or workflow is None
        or workflow.status != WorkflowStatus.ACTIVE
    ):
        # Mismo 404 para "no existe" y "desactivado": no se revela qué ids son válidos.
        raise NotFoundError("Destino de webhook no encontrado.")
    return trigger


async def receive(
    session: AsyncSession,
    settings: Settings,
    cipher: CredentialCipher,
    trigger: WebhookTrigger,
    *,
    body: bytes,
    signature: str | None,
    event_id: str | None,
    context: RequestContext,
) -> tuple[runs.CreatedRun, bool]:
    """Verifica la firma y crea la ejecución. Devuelve (ejecución, es_duplicado)."""
    try:
        secret = decrypt_secret(cipher, trigger.id, TRIGGER_SECRET_SCOPE, trigger.secret_encrypted)
        timestamp = signing.verify(
            signature,
            secret,
            body,
            tolerance_seconds=settings.webhook_signature_tolerance_seconds,
        )
    except (signing.SignatureError, CredentialDecryptionError) as exc:
        logger.warning("webhook_signature_rejected", trigger_id=str(trigger.id), reason=str(exc))
        raise UnauthorizedError("Firma del webhook no válida o caducada.") from exc

    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise InvalidPayloadError("El cuerpo debe ser JSON.") from exc
    if not isinstance(payload, dict):
        raise InvalidPayloadError("El cuerpo debe ser un objeto JSON.")

    if event_id is not None and not _EVENT_ID.fullmatch(event_id):
        raise InvalidPayloadError("El id de evento solo admite [A-Za-z0-9._:-] (máx. 80).")
    # Sin id explícito, el propio contenido firmado identifica el evento: una repetición
    # exacta (mismo timestamp y cuerpo) se deduplica.
    key = event_id or hashlib.sha256(f"{timestamp}.".encode() + body).hexdigest()[:64]

    created = await runs.create_run(
        session,
        settings,
        workflow_id=trigger.workflow_id,
        data=RunCreate(input=payload, idempotency_key=f"wh:{trigger.id.hex[:12]}:{key}"),
        actor_id=None,
        trigger_type="webhook",
        context=context,
        strict_input=False,
    )
    await session.execute(
        update(WebhookTrigger)
        .where(WebhookTrigger.id == trigger.id)
        .values(last_received_at=utcnow())
    )
    await session.commit()
    return created, not created.created


# ------------------------------------------------------------------ suscripciones (salientes)


async def create_subscription(
    session: AsyncSession,
    settings: Settings,
    cipher: CredentialCipher,
    actor: User,
    data: SubscriptionCreate,
    context: RequestContext,
) -> tuple[WebhookSubscription, str]:
    check_target_url(data.url, settings)
    if data.workflow_id is not None:
        await _active_workflow(session, data.workflow_id)
    sub_id = uuid.uuid4()
    secret = data.secret or signing.generate_secret()
    subscription = WebhookSubscription(
        id=sub_id,
        name=data.name,
        url=data.url,
        events=data.events,
        workflow_id=data.workflow_id,
        secret_encrypted=encrypt_secret(cipher, sub_id, SUBSCRIPTION_SECRET_SCOPE, secret),
        created_by=actor.id,
    )
    session.add(subscription)
    _audit(
        session,
        WebhookAction.SUBSCRIPTION_CREATED,
        actor,
        context,
        "webhook_subscription",
        sub_id,
        {"url": data.url, "events": data.events},
    )
    await session.commit()
    return subscription, secret


async def list_subscriptions(session: AsyncSession) -> list[WebhookSubscription]:
    return list(
        await session.scalars(select(WebhookSubscription).order_by(WebhookSubscription.name))
    )


async def get_subscription(
    session: AsyncSession, subscription_id: uuid.UUID, *, lock: bool = False
) -> WebhookSubscription:
    found = await session.get(WebhookSubscription, subscription_id, with_for_update=lock)
    if found is None:
        raise NotFoundError("La suscripción no existe.")
    return found


async def update_subscription(
    session: AsyncSession,
    settings: Settings,
    actor: User,
    subscription_id: uuid.UUID,
    data: SubscriptionUpdate,
    context: RequestContext,
) -> WebhookSubscription:
    subscription = await get_subscription(session, subscription_id, lock=True)
    changes = data.model_dump(exclude_none=True)
    if "url" in changes:
        check_target_url(changes["url"], settings)
    for key, value in changes.items():
        setattr(subscription, key, value)
    _audit(
        session,
        WebhookAction.SUBSCRIPTION_UPDATED,
        actor,
        context,
        "webhook_subscription",
        subscription_id,
        changes,
    )
    await session.commit()
    await session.refresh(subscription)
    return subscription


async def rotate_subscription_secret(
    session: AsyncSession,
    cipher: CredentialCipher,
    actor: User,
    subscription_id: uuid.UUID,
    context: RequestContext,
) -> tuple[WebhookSubscription, str]:
    subscription = await get_subscription(session, subscription_id, lock=True)
    secret = signing.generate_secret()
    subscription.secret_encrypted = encrypt_secret(
        cipher, subscription.id, SUBSCRIPTION_SECRET_SCOPE, secret
    )
    _audit(
        session,
        WebhookAction.SUBSCRIPTION_SECRET_ROTATED,
        actor,
        context,
        "webhook_subscription",
        subscription_id,
    )
    await session.commit()
    await session.refresh(subscription)
    return subscription, secret


async def delete_subscription(
    session: AsyncSession, actor: User, subscription_id: uuid.UUID, context: RequestContext
) -> None:
    subscription = await get_subscription(session, subscription_id, lock=True)
    await session.delete(subscription)
    _audit(
        session,
        WebhookAction.SUBSCRIPTION_DELETED,
        actor,
        context,
        "webhook_subscription",
        subscription_id,
    )
    await session.commit()


async def ping(session: AsyncSession, subscription_id: uuid.UUID) -> DispatchItem:
    subscription = await get_subscription(session, subscription_id)
    delivery = new_delivery(subscription.id, WebhookEvent.PING.value, {"message": "ping"})
    session.add(delivery)
    await session.commit()
    return DispatchItem(delivery.id, task=DELIVER_WEBHOOK_TASK)


async def list_deliveries(
    session: AsyncSession,
    subscription_id: uuid.UUID,
    *,
    status: DeliveryStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[WebhookDelivery], int]:
    await get_subscription(session, subscription_id)
    filters = [WebhookDelivery.subscription_id == subscription_id]
    if status is not None:
        filters.append(WebhookDelivery.status == status)
    total = (
        await session.scalar(select(func.count()).select_from(WebhookDelivery).where(*filters)) or 0
    )
    rows = await session.scalars(
        select(WebhookDelivery)
        .where(*filters)
        .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id)
        .limit(limit)
        .offset(offset)
    )
    return list(rows), total


async def requeue_delivery(
    session: AsyncSession, actor: User, delivery_id: uuid.UUID, context: RequestContext
) -> tuple[WebhookDelivery, DispatchItem]:
    """Reenvía una entrega muerta (o ya entregada) con el MISMO event_id."""
    delivery = await session.get(WebhookDelivery, delivery_id, with_for_update=True)
    if delivery is None:
        raise NotFoundError("La entrega no existe.")
    if delivery.status not in (DeliveryStatus.DEAD, DeliveryStatus.DELIVERED):
        raise ConflictError("Solo se reenvían entregas terminadas (dead o delivered).")
    delivery.status = DeliveryStatus.PENDING
    delivery.attempt_count = 0
    delivery.next_attempt_at = None
    delivery.last_dispatched_at = utcnow()
    _audit(
        session, WebhookAction.DELIVERY_REQUEUED, actor, context, "webhook_delivery", delivery_id
    )
    await session.commit()
    await session.refresh(delivery)
    return delivery, DispatchItem(delivery.id, task=DELIVER_WEBHOOK_TASK)
