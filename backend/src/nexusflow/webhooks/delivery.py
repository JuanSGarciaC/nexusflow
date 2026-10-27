"""Entrega de webhooks salientes (relay del outbox).

Contrato con el receptor: entrega *al menos una vez*. Cada evento lleva un id estable
(`X-NexusFlow-Event-Id`) para que el receptor deduplique, y una firma HMAC sobre el cuerpo
exacto. Se reintenta cualquier respuesta que no sea 2xx con backoff exponencial; agotados
los intentos, la entrega queda `dead` (dead-letter) y se puede reenviar a mano.
"""

from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexusflow.core.config import Settings
from nexusflow.core.logging import get_logger
from nexusflow.db.types import utcnow
from nexusflow.engine.dispatch import DELIVER_WEBHOOK_TASK, Dispatcher, DispatchItem
from nexusflow.engine.http_client import EgressPolicy, OutboundHttpError, send_request
from nexusflow.integrations.crypto import CredentialCipher, CredentialDecryptionError
from nexusflow.webhooks import signing
from nexusflow.webhooks.models import (
    DELIVERY_CLAIMABLE,
    DeliveryStatus,
    WebhookDelivery,
    WebhookSubscription,
)

logger = get_logger(__name__)

SUBSCRIPTION_SECRET_SCOPE = "webhook-subscription"  # noqa: S105  # ámbito, no un secreto
REDISPATCH_AFTER = timedelta(seconds=60)


def delivery_backoff(attempt: int, rng: random.Random | None = None) -> float:
    """30 s, 1 min, 2 min, 4 min… hasta 1 h, con jitter del ±20 %."""
    base = min(3600.0, 30.0 * 2 ** max(attempt - 1, 0))
    jitter = (rng or random).uniform(0.8, 1.2)  # aleatoriedad no criptográfica
    return float(round(base * jitter, 3))


def decrypt_secret(cipher: CredentialCipher, owner_id: uuid.UUID, scope: str, token: str) -> str:
    secret: str = cipher.decrypt(owner_id, scope, token)["secret"]
    return secret


def encrypt_secret(cipher: CredentialCipher, owner_id: uuid.UUID, scope: str, secret: str) -> str:
    return cipher.encrypt(owner_id, scope, {"secret": secret})


@dataclass(frozen=True, slots=True)
class _Claim:
    delivery_id: uuid.UUID
    attempt: int
    url: str
    body: bytes
    secret: str
    event_id: str
    event_type: str


class WebhookDeliverer:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        http: httpx.AsyncClient,
        egress: EgressPolicy,
        cipher: CredentialCipher,
        dispatcher: Dispatcher,
    ) -> None:
        self._sessions = sessions
        self._settings = settings
        self._http = http
        self._egress = egress
        self._cipher = cipher
        self._dispatcher = dispatcher

    async def deliver(self, delivery_id: uuid.UUID) -> str:
        claim = await self._claim(delivery_id)
        if claim is None:
            return "skipped"
        status_code: int | None = None
        error: str | None = None
        try:
            result = await send_request(
                self._http,
                method="POST",
                url=claim.url,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "NexusFlow-Webhooks/1",
                    signing.EVENT_ID_HEADER: claim.event_id,
                    signing.EVENT_TYPE_HEADER: claim.event_type,
                    signing.SIGNATURE_HEADER: signing.sign(claim.secret, claim.body),
                },
                params={},
                json_body=None,
                content=claim.body,
                timeout_seconds=self._settings.webhook_delivery_timeout_seconds,
                policy=self._egress,
            )
            status_code = result.status_code
            if not 200 <= status_code < 300:
                error = f"Respuesta {status_code}"
        except OutboundHttpError as exc:
            status_code = exc.http_status
            error = f"{exc.error_class}: {exc.message}"[:500]
        return await self._record(claim, status_code, error)

    async def _claim(self, delivery_id: uuid.UUID) -> _Claim | None:
        async with self._sessions() as session:
            delivery = await session.get(
                WebhookDelivery, delivery_id, with_for_update={"skip_locked": True}
            )
            now = utcnow()
            if delivery is None or delivery.status not in DELIVERY_CLAIMABLE:
                return None
            if delivery.next_attempt_at is not None and delivery.next_attempt_at > now:
                return None
            subscription = await session.get(WebhookSubscription, delivery.subscription_id)
            if subscription is None or not subscription.is_active:
                delivery.status = DeliveryStatus.DEAD
                delivery.last_error = "La suscripción está desactivada"
                await session.commit()
                return None
            try:
                secret = decrypt_secret(
                    self._cipher,
                    subscription.id,
                    SUBSCRIPTION_SECRET_SCOPE,
                    subscription.secret_encrypted,
                )
            except CredentialDecryptionError:
                delivery.status = DeliveryStatus.DEAD
                delivery.last_error = "No se pudo descifrar el secreto de la suscripción"
                await session.commit()
                logger.error("webhook_secret_unreadable", subscription_id=str(subscription.id))
                return None
            delivery.status = DeliveryStatus.DELIVERING
            delivery.attempt_count += 1
            delivery.lease_expires_at = now + timedelta(
                seconds=self._settings.webhook_delivery_timeout_seconds + 30
            )
            claim = _Claim(
                delivery_id=delivery.id,
                attempt=delivery.attempt_count,
                url=subscription.url,
                body=json.dumps(delivery.payload, separators=(",", ":")).encode(),
                secret=secret,
                event_id=str(delivery.event_id),
                event_type=delivery.event_type,
            )
            await session.commit()
            return claim

    async def _record(self, claim: _Claim, status_code: int | None, error: str | None) -> str:
        to_dispatch: list[DispatchItem] = []
        async with self._sessions() as session:
            delivery = await session.get(WebhookDelivery, claim.delivery_id, with_for_update=True)
            if (
                delivery is None
                or delivery.status != DeliveryStatus.DELIVERING
                or delivery.attempt_count != claim.attempt
            ):
                return "stale"
            now = utcnow()
            delivery.last_status_code = status_code
            delivery.lease_expires_at = None
            if error is None:
                delivery.status = DeliveryStatus.DELIVERED
                delivery.delivered_at = now
                delivery.last_error = None
                label = "delivered"
            elif delivery.attempt_count >= delivery.max_attempts:
                delivery.status = DeliveryStatus.DEAD
                delivery.last_error = error
                label = "dead"
                logger.warning("webhook_dead_lettered", delivery_id=str(delivery.id))
            else:
                delay = delivery_backoff(delivery.attempt_count)
                delivery.status = DeliveryStatus.RETRY_SCHEDULED
                delivery.next_attempt_at = now + timedelta(seconds=delay)
                delivery.last_error = error
                if delay <= self._settings.max_dispatch_countdown_seconds:
                    delivery.last_dispatched_at = now
                    to_dispatch.append(
                        DispatchItem(delivery.id, countdown=delay, task=DELIVER_WEBHOOK_TASK)
                    )
                label = "retry_scheduled"
            await session.commit()
        await self._dispatcher.dispatch(to_dispatch)
        return label

    async def sweep(self, *, limit: int = 200) -> tuple[int, int]:
        """Reencola entregas listas que nadie procesa y recupera las de workers caídos."""
        now = utcnow()
        items: list[DispatchItem] = []
        recovered = 0
        async with self._sessions() as session:
            stuck = await session.scalars(
                select(WebhookDelivery)
                .where(
                    WebhookDelivery.status == DeliveryStatus.DELIVERING,
                    WebhookDelivery.lease_expires_at <= now,
                )
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
            for delivery in stuck:
                # Webhooks son at-least-once: reintentar tras un worker caído es seguro.
                delivery.status = DeliveryStatus.RETRY_SCHEDULED
                delivery.next_attempt_at = now
                delivery.lease_expires_at = None
                delivery.last_error = "worker_lost"
                recovered += 1
            due = await session.scalars(
                select(WebhookDelivery)
                .where(
                    WebhookDelivery.status.in_(list(DELIVERY_CLAIMABLE)),
                    or_(
                        WebhookDelivery.next_attempt_at.is_(None),
                        WebhookDelivery.next_attempt_at <= now,
                    ),
                    or_(
                        WebhookDelivery.last_dispatched_at.is_(None),
                        WebhookDelivery.last_dispatched_at <= now - REDISPATCH_AFTER,
                    ),
                )
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
            for delivery in due:
                delivery.last_dispatched_at = now
                items.append(DispatchItem(delivery.id, task=DELIVER_WEBHOOK_TASK))
            await session.commit()
        await self._dispatcher.dispatch(items)
        return len(items), recovered
