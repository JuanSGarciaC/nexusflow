"""Punto de entrada de la API (application factory)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from nexusflow import __version__
from nexusflow.audit.router import router as audit_router
from nexusflow.auth.router import auth_router, users_router
from nexusflow.core.config import Settings, get_settings
from nexusflow.core.errors import register_error_handlers
from nexusflow.core.logging import configure_logging, get_logger
from nexusflow.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from nexusflow.core.rate_limit import RateLimiter
from nexusflow.db.session import create_engine, create_session_factory
from nexusflow.engine.celery_config import create_celery
from nexusflow.engine.dispatch import CeleryDispatcher, Dispatcher
from nexusflow.engine.router import router as runs_router
from nexusflow.integrations.crypto import CredentialCipher
from nexusflow.integrations.router import router as credentials_router
from nexusflow.observability import analytics, health
from nexusflow.observability.prometheus import AppMetrics, PrometheusMiddleware
from nexusflow.observability.prometheus import router as prometheus_router
from nexusflow.schedules.router import router as schedules_router
from nexusflow.webhooks.router import inbound_router as hooks_router
from nexusflow.webhooks.router import router as webhooks_router
from nexusflow.workflows.router import router as workflows_router

logger = get_logger(__name__)


def create_app(settings: Settings | None = None, dispatcher: Dispatcher | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        redis = Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_connect_timeout=settings.readiness_timeout_seconds,
            socket_timeout=settings.readiness_timeout_seconds,
        )
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.redis = redis
        app.state.rate_limiter = RateLimiter(redis)
        app.state.cipher = CredentialCipher(settings.credentials_encryption_keys.get_secret_value())
        # La API solo produce mensajes; se puede inyectar otro dispatcher en pruebas.
        app.state.dispatcher = dispatcher or CeleryDispatcher(create_celery(settings))
        logger.info("startup", env=settings.env, version=__version__)
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()
            logger.info("shutdown")

    docs = settings.docs_enabled
    app = FastAPI(
        title="NexusFlow API",
        version=__version__,
        description="Plataforma de automatización de procesos empresariales.",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.metrics = AppMetrics()

    register_error_handlers(app)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID", "Retry-After"],
        )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    app.add_middleware(PrometheusMiddleware, metrics=app.state.metrics)
    # Añadido al final para ser el middleware más externo: todo log lleva request_id.
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(workflows_router)
    app.include_router(runs_router)
    app.include_router(credentials_router)
    app.include_router(schedules_router)
    app.include_router(webhooks_router)
    app.include_router(hooks_router)
    app.include_router(analytics.router)
    app.include_router(audit_router)
    app.include_router(prometheus_router)
    return app


def app_factory() -> FastAPI:
    """Usado por uvicorn con --factory, para no crear la app al importar el módulo."""
    return create_app()
