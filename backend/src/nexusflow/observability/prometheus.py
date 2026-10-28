"""Métricas Prometheus del proceso API.

Dos fuentes, con criterios distintos:

- **HTTP (por proceso):** contador y histograma por método, *plantilla* de ruta y código
  de estado. Se usa la plantilla (`/api/v1/runs/{run_id}`) y no la ruta real para que la
  cardinalidad no crezca con cada id.
- **Negocio (desde PostgreSQL):** ejecuciones y pasos por estado, y entregas de webhook
  pendientes o en dead-letter. Se calculan al leer `/metrics` consultando la base de datos,
  porque es la fuente de verdad compartida por todos los procesos API y worker. Sumar
  contadores en memoria de varios procesos daría cifras inconsistentes.

Cada aplicación usa su propio `CollectorRegistry` (no el global): crear varias apps en el
mismo proceso, como hacen las pruebas, no registra métricas duplicadas.
"""

from __future__ import annotations

import hmac
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexusflow.core.config import Settings
from nexusflow.core.errors import NotFoundError, UnauthorizedError
from nexusflow.db.session import get_session
from nexusflow.engine.models import RunStatus, StepRun, StepStatus, WorkflowRun
from nexusflow.webhooks.models import DeliveryStatus, WebhookDelivery

# Latencias de una API: desde 5 ms hasta 10 s.
_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
UNMATCHED_ROUTE = "unmatched"


class AppMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.http_requests = Counter(
            "nexusflow_http_requests",
            "Peticiones HTTP atendidas",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "nexusflow_http_request_duration_seconds",
            "Duración de las peticiones HTTP",
            ["method", "route"],
            buckets=_BUCKETS,
            registry=self.registry,
        )
        self.runs = Gauge(
            "nexusflow_runs",
            "Ejecuciones por estado (desde PostgreSQL)",
            ["status"],
            registry=self.registry,
        )
        self.steps_active = Gauge(
            "nexusflow_steps_active",
            "Pasos no terminados por estado (desde PostgreSQL)",
            ["status"],
            registry=self.registry,
        )
        self.webhook_deliveries = Gauge(
            "nexusflow_webhook_deliveries_open",
            "Entregas de webhook pendientes o en dead-letter (desde PostgreSQL)",
            ["status"],
            registry=self.registry,
        )

    def observe_request(self, method: str, route: str, status: int, seconds: float) -> None:
        self.http_requests.labels(method, route, str(status)).inc()
        self.http_duration.labels(method, route).observe(seconds)

    async def refresh_business_gauges(self, session: AsyncSession) -> None:
        runs = dict(
            (
                await session.execute(
                    select(WorkflowRun.status, func.count()).group_by(WorkflowRun.status)
                )
            ).all()
        )
        for run_status in RunStatus:
            self.runs.labels(run_status.value).set(runs.get(run_status, 0))

        active_states = [
            StepStatus.PENDING,
            StepStatus.RUNNING,
            StepStatus.RETRY_SCHEDULED,
            StepStatus.NEEDS_REVIEW,
        ]
        steps = dict(
            (
                await session.execute(
                    select(StepRun.status, func.count())
                    .where(StepRun.status.in_(active_states))
                    .group_by(StepRun.status)
                )
            ).all()
        )
        for step_status in active_states:
            self.steps_active.labels(step_status.value).set(steps.get(step_status, 0))

        open_states = [
            DeliveryStatus.PENDING,
            DeliveryStatus.RETRY_SCHEDULED,
            DeliveryStatus.DELIVERING,
            DeliveryStatus.DEAD,
        ]
        deliveries = dict(
            (
                await session.execute(
                    select(WebhookDelivery.status, func.count())
                    .where(WebhookDelivery.status.in_(open_states))
                    .group_by(WebhookDelivery.status)
                )
            ).all()
        )
        for delivery_status in open_states:
            self.webhook_deliveries.labels(delivery_status.value).set(
                deliveries.get(delivery_status, 0)
            )


class PrometheusMiddleware:
    """Mide cada petición HTTP. Va por dentro de RequestContextMiddleware."""

    def __init__(self, app: ASGIApp, metrics: AppMetrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        status_code = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = getattr(scope.get("route"), "path", None) or UNMATCHED_ROUTE
            if route != "/metrics":  # el propio scrape no se cuenta
                self.metrics.observe_request(
                    scope.get("method", "?"), route, status_code, time.perf_counter() - start
                )


router = APIRouter(tags=["observability"])


def _authorize_scrape(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> None:
    settings: Settings = request.app.state.settings
    if settings.metrics_token is None:
        raise NotFoundError()  # deshabilitado: no se revela que el endpoint existe
    expected = f"Bearer {settings.metrics_token.get_secret_value()}"
    # Comparación en tiempo constante: no filtra cuántos caracteres coinciden.
    if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise UnauthorizedError("Token de métricas inválido.")


@router.get(
    "/metrics",
    summary="Métricas en formato Prometheus",
    description="Requiere `Authorization: Bearer <NEXUSFLOW_METRICS_TOKEN>`. "
    "Devuelve 404 si no hay token configurado.",
    response_class=Response,
    responses={200: {"content": {CONTENT_TYPE_LATEST: {}}}},
    dependencies=[Depends(_authorize_scrape)],
)
async def metrics(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> Response:
    app_metrics: AppMetrics = request.app.state.metrics
    await app_metrics.refresh_business_gauges(session)
    return Response(generate_latest(app_metrics.registry), media_type=CONTENT_TYPE_LATEST)
