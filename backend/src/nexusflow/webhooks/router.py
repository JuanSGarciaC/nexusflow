"""Endpoints de webhooks: gestión (requiere `webhooks:manage`) y recepción pública."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from nexusflow.auth.dependencies import (
    ContextDep,
    RateLimiterDep,
    SessionDep,
    SettingsDep,
    require_permission,
)
from nexusflow.auth.models import User
from nexusflow.auth.permissions import Permission
from nexusflow.core.rate_limit import RateLimit
from nexusflow.engine.router import DispatcherDep
from nexusflow.integrations.router import CipherDep
from nexusflow.webhooks import service
from nexusflow.webhooks.models import DeliveryStatus, WebhookTrigger
from nexusflow.webhooks.schemas import (
    DeliveryPage,
    DeliveryView,
    InboundAccepted,
    SubscriptionCreate,
    SubscriptionUpdate,
    SubscriptionView,
    SubscriptionWithSecret,
    TriggerCreate,
    TriggerUpdate,
    TriggerView,
    TriggerWithSecret,
)
from nexusflow.webhooks.signing import EVENT_ID_HEADER, SIGNATURE_HEADER

router = APIRouter(prefix="/api/v1", tags=["webhooks"])
inbound_router = APIRouter(prefix="/api/v1/hooks", tags=["webhooks (entrada pública)"])

_PROBLEM: dict[str, object] = {"content": {"application/problem+json": {}}}
Manager = Annotated[User, Depends(require_permission(Permission.WEBHOOKS_MANAGE))]


def _trigger_view(trigger: WebhookTrigger) -> TriggerView:
    return TriggerView(
        id=trigger.id,
        workflow_id=trigger.workflow_id,
        name=trigger.name,
        is_active=trigger.is_active,
        rate_limit_per_minute=trigger.rate_limit_per_minute,
        url_path=f"/api/v1/hooks/{trigger.id}",
        created_at=trigger.created_at,
        last_received_at=trigger.last_received_at,
    )


# ------------------------------------------------------------------ recepción pública


@inbound_router.post(
    "/{trigger_id}",
    response_model=InboundAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Recibir un webhook firmado y lanzar el flujo",
    description=(
        "Sin JWT: se autentica con `X-NexusFlow-Signature: t=<unix>,v1=<hmac>` donde "
        "`hmac = HMAC-SHA256(secreto, f'{t}.' + cuerpo)`. Se rechazan firmas de más de 5 "
        "minutos. Opcional `X-NexusFlow-Event-Id` para deduplicar reenvíos."
    ),
    responses={
        200: {"model": InboundAccepted, "description": "Evento ya recibido (duplicado)"},
        401: {"description": "Firma ausente, inválida o caducada", **_PROBLEM},
        404: {"description": "Destino inexistente o desactivado", **_PROBLEM},
        429: {"description": "Límite de uso del trigger", **_PROBLEM},
    },
)
async def receive(
    trigger_id: uuid.UUID,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    cipher: CipherDep,
    limiter: RateLimiterDep,
    dispatcher: DispatcherDep,
    context: ContextDep,
    signature: Annotated[str | None, Header(alias=SIGNATURE_HEADER, max_length=512)] = None,
    event_id: Annotated[str | None, Header(alias=EVENT_ID_HEADER, max_length=100)] = None,
) -> InboundAccepted:
    trigger = await service.find_active_trigger(session, trigger_id)
    await limiter.hit(
        RateLimit("webhook_trigger", trigger.rate_limit_per_minute, 60), str(trigger.id)
    )
    body = await request.body()  # bytes exactos: la firma se calcula sobre ellos
    created, duplicate = await service.receive(
        session,
        settings,
        cipher,
        trigger,
        body=body,
        signature=signature,
        event_id=event_id,
        context=context,
    )
    await dispatcher.dispatch(created.dispatch)
    if duplicate:
        response.status_code = status.HTTP_200_OK
    return InboundAccepted(run_id=created.run.id, duplicate=duplicate)


# ------------------------------------------------------------------ triggers


@router.post(
    "/workflows/{workflow_id}/triggers",
    response_model=TriggerWithSecret,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un webhook entrante para un flujo (el secreto se muestra una vez)",
)
async def create_trigger(
    workflow_id: uuid.UUID,
    data: TriggerCreate,
    session: SessionDep,
    cipher: CipherDep,
    context: ContextDep,
    actor: Manager,
) -> TriggerWithSecret:
    trigger, secret = await service.create_trigger(
        session, cipher, actor, workflow_id, data, context
    )
    return TriggerWithSecret(**_trigger_view(trigger).model_dump(), secret=secret)


@router.get(
    "/workflows/{workflow_id}/triggers",
    response_model=list[TriggerView],
    summary="Webhooks entrantes de un flujo",
)
async def list_triggers(
    workflow_id: uuid.UUID, session: SessionDep, _: Manager
) -> list[TriggerView]:
    return [_trigger_view(t) for t in await service.list_triggers(session, workflow_id)]


@router.patch(
    "/triggers/{trigger_id}", response_model=TriggerView, summary="Activar, renombrar o limitar"
)
async def update_trigger(
    trigger_id: uuid.UUID,
    data: TriggerUpdate,
    session: SessionDep,
    context: ContextDep,
    actor: Manager,
) -> TriggerView:
    return _trigger_view(await service.update_trigger(session, actor, trigger_id, data, context))


@router.post(
    "/triggers/{trigger_id}/rotate-secret",
    response_model=TriggerWithSecret,
    summary="Generar un secreto nuevo (el anterior deja de valer)",
)
async def rotate_trigger_secret(
    trigger_id: uuid.UUID,
    session: SessionDep,
    cipher: CipherDep,
    context: ContextDep,
    actor: Manager,
) -> TriggerWithSecret:
    trigger, secret = await service.rotate_trigger_secret(
        session, cipher, actor, trigger_id, context
    )
    return TriggerWithSecret(**_trigger_view(trigger).model_dump(), secret=secret)


@router.delete("/triggers/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trigger(
    trigger_id: uuid.UUID, session: SessionDep, context: ContextDep, actor: Manager
) -> Response:
    await service.delete_trigger(session, actor, trigger_id, context)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ suscripciones


@router.post(
    "/webhook-subscriptions",
    response_model=SubscriptionWithSecret,
    status_code=status.HTTP_201_CREATED,
    summary="Suscribir una URL a eventos de ejecuciones (el secreto se muestra una vez)",
)
async def create_subscription(
    data: SubscriptionCreate,
    session: SessionDep,
    settings: SettingsDep,
    cipher: CipherDep,
    context: ContextDep,
    actor: Manager,
) -> SubscriptionWithSecret:
    subscription, secret = await service.create_subscription(
        session, settings, cipher, actor, data, context
    )
    return SubscriptionWithSecret(
        **SubscriptionView.model_validate(subscription).model_dump(), secret=secret
    )


@router.get("/webhook-subscriptions", response_model=list[SubscriptionView])
async def list_subscriptions(session: SessionDep, _: Manager) -> list[SubscriptionView]:
    return [SubscriptionView.model_validate(s) for s in await service.list_subscriptions(session)]


@router.patch("/webhook-subscriptions/{subscription_id}", response_model=SubscriptionView)
async def update_subscription(
    subscription_id: uuid.UUID,
    data: SubscriptionUpdate,
    session: SessionDep,
    settings: SettingsDep,
    context: ContextDep,
    actor: Manager,
) -> SubscriptionView:
    subscription = await service.update_subscription(
        session, settings, actor, subscription_id, data, context
    )
    return SubscriptionView.model_validate(subscription)


@router.post(
    "/webhook-subscriptions/{subscription_id}/rotate-secret",
    response_model=SubscriptionWithSecret,
)
async def rotate_subscription_secret(
    subscription_id: uuid.UUID,
    session: SessionDep,
    cipher: CipherDep,
    context: ContextDep,
    actor: Manager,
) -> SubscriptionWithSecret:
    subscription, secret = await service.rotate_subscription_secret(
        session, cipher, actor, subscription_id, context
    )
    return SubscriptionWithSecret(
        **SubscriptionView.model_validate(subscription).model_dump(), secret=secret
    )


@router.delete("/webhook-subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_subscription(
    subscription_id: uuid.UUID, session: SessionDep, context: ContextDep, actor: Manager
) -> Response:
    await service.delete_subscription(session, actor, subscription_id, context)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/webhook-subscriptions/{subscription_id}/ping",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enviar un evento de prueba",
)
async def ping(
    subscription_id: uuid.UUID, session: SessionDep, dispatcher: DispatcherDep, _: Manager
) -> Response:
    await dispatcher.dispatch([await service.ping(session, subscription_id)])
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.get(
    "/webhook-subscriptions/{subscription_id}/deliveries",
    response_model=DeliveryPage,
    summary="Historial de entregas (incluye las que están en dead-letter)",
)
async def list_deliveries(
    subscription_id: uuid.UUID,
    session: SessionDep,
    _: Manager,
    status_filter: Annotated[DeliveryStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> DeliveryPage:
    items, total = await service.list_deliveries(
        session, subscription_id, status=status_filter, limit=limit, offset=offset
    )
    return DeliveryPage(
        items=[DeliveryView.model_validate(d) for d in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/webhook-deliveries/{delivery_id}/redeliver",
    response_model=DeliveryView,
    summary="Reenviar una entrega (mismo event_id)",
)
async def redeliver(
    delivery_id: uuid.UUID,
    session: SessionDep,
    dispatcher: DispatcherDep,
    context: ContextDep,
    actor: Manager,
) -> DeliveryView:
    delivery, item = await service.requeue_delivery(session, actor, delivery_id, context)
    await dispatcher.dispatch([item])
    return DeliveryView.model_validate(delivery)
