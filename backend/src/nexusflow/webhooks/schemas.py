"""Esquemas de la API de webhooks."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nexusflow.webhooks.models import DeliveryStatus, WebhookEvent

SUBSCRIBABLE = [e.value for e in WebhookEvent if e != WebhookEvent.PING]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_events(events: list[str]) -> list[str]:
    unknown = sorted(set(events) - set(SUBSCRIBABLE))
    if unknown:
        raise ValueError(f"Eventos desconocidos: {unknown}. Válidos: {SUBSCRIBABLE}")
    return sorted(set(events))


class TriggerCreate(_Strict):
    name: str = Field(min_length=1, max_length=100)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=6000)


class TriggerUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=6000)


class TriggerView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    name: str
    is_active: bool
    rate_limit_per_minute: int
    url_path: str
    created_at: datetime
    last_received_at: datetime | None


class TriggerWithSecret(TriggerView):
    secret: str = Field(description="Se muestra solo ahora. Guárdalo en el sistema emisor.")


class InboundAccepted(BaseModel):
    run_id: uuid.UUID
    duplicate: bool


class SubscriptionCreate(_Strict):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=8, max_length=2048)
    events: list[str] = Field(min_length=1, max_length=len(SUBSCRIBABLE))
    workflow_id: uuid.UUID | None = None
    secret: str | None = Field(
        default=None,
        min_length=32,
        max_length=200,
        description="Opcional: si no se indica, se genera uno (whsec_...)",
    )

    @field_validator("events")
    @classmethod
    def _known_events(cls, events: list[str]) -> list[str]:
        return _check_events(events)


class SubscriptionUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, min_length=8, max_length=2048)
    events: list[str] | None = Field(default=None, min_length=1)
    is_active: bool | None = None

    @field_validator("events")
    @classmethod
    def _known_events(cls, events: list[str] | None) -> list[str] | None:
        return None if events is None else _check_events(events)


class SubscriptionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    url: str
    events: list[str]
    workflow_id: uuid.UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SubscriptionWithSecret(SubscriptionView):
    secret: str = Field(description="Se muestra solo ahora. Úsalo para verificar las firmas.")


class DeliveryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subscription_id: uuid.UUID
    event_id: uuid.UUID
    event_type: str
    status: DeliveryStatus
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None
    last_status_code: int | None
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None
    payload: dict[str, Any]


class DeliveryPage(BaseModel):
    items: list[DeliveryView]
    total: int
    limit: int
    offset: int
