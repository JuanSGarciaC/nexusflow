"""Webhooks entrantes (triggers) y salientes (suscripciones + outbox de entregas)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexusflow.db.base import Base
from nexusflow.db.types import TimestampMixin, UUIDPrimaryKeyMixin


class WebhookEvent(StrEnum):
    RUN_SUCCEEDED = "run.succeeded"
    RUN_FAILED = "run.failed"
    RUN_NEEDS_REVIEW = "run.needs_review"
    RUN_TIMED_OUT = "run.timed_out"
    RUN_CANCELLED = "run.cancelled"
    PING = "ping"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERING = "delivering"
    RETRY_SCHEDULED = "retry_scheduled"
    DELIVERED = "delivered"
    DEAD = "dead"  # agotó los intentos (dead-letter); se puede reenviar a mano


DELIVERY_CLAIMABLE = frozenset({DeliveryStatus.PENDING, DeliveryStatus.RETRY_SCHEDULED})


class WebhookTrigger(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Punto de entrada público: POST /api/v1/hooks/{id} con firma HMAC."""

    __tablename__ = "webhook_triggers"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    last_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookSubscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Destino externo que recibe eventos de ejecuciones, firmados."""

    __tablename__ = "webhook_subscriptions"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    events: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False)
    # NULL = eventos de todos los flujos.
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class WebhookDelivery(UUIDPrimaryKeyMixin, Base):
    """Fila del outbox: se crea en la misma transacción que el evento que notifica."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index(
            "ix_webhook_deliveries_dispatch",
            "status",
            "next_attempt_at",
            postgresql_where=text("status IN ('pending', 'retry_scheduled', 'delivering')"),
        ),
        Index("ix_webhook_deliveries_subscription", "subscription_id", "created_at"),
    )

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"), nullable=False
    )
    # Mismo id en todos los reintentos: el receptor deduplica con él.
    event_id: Mapped[uuid.UUID] = mapped_column(nullable=False, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(
            DeliveryStatus,
            name="delivery_status",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda items: [i.value for i in items],
        ),
        nullable=False,
        default=DeliveryStatus.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
