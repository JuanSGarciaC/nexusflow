"""Punto de entrada de la API (application factory)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from nexusflow import __version__
from nexusflow.auth.router import auth_router, users_router
from nexusflow.core.config import Settings, get_settings
from nexusflow.core.errors import register_error_handlers
from nexusflow.core.logging import configure_logging, get_logger
from nexusflow.core.middleware import RequestContextMiddleware
from nexusflow.core.rate_limit import RateLimiter
from nexusflow.db.session import create_engine, create_session_factory
from nexusflow.observability import health

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
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
    # Añadido al final para ser el middleware más externo: todo log lleva request_id.
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router)
    app.include_router(auth_router)
    app.include_router(users_router)
    return app


def app_factory() -> FastAPI:
    """Usado por uvicorn con --factory, para no crear la app al importar el módulo."""
    return create_app()
