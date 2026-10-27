"""Infraestructura de pruebas de integración: base de datos y Redis reales.

- Las migraciones se aplican una vez por sesión de pruebas (lo mismo que en producción).
- Antes de cada prueba se vacían las tablas y la base de Redis de pruebas.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis import Redis as SyncRedis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from nexusflow.auth.models import User
from nexusflow.auth.passwords import hash_password
from nexusflow.auth.permissions import Role
from nexusflow.core.config import Settings
from nexusflow.main import create_app
from tests.conftest import make_settings

BACKEND_DIR = Path(__file__).resolve().parents[2]

STRONG_PASSWORD = "correcto-caballo-bateria-grapa"


def db_url() -> str:
    return os.environ["NEXUSFLOW_TEST_DATABASE_URL"]


def redis_url() -> str:
    return os.environ["NEXUSFLOW_TEST_REDIS_URL"]


def integration_settings(**overrides: object) -> Settings:
    return make_settings(database_url=db_url(), redis_url=redis_url(), **overrides)


def run_db[T](operation: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Ejecuta una operación con una sesión propia (fuera del event loop de la app)."""

    async def _run() -> T:
        engine = create_async_engine(db_url(), poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await operation(session)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def create_user(email: str, role: Role = Role.VIEWER, *, active: bool = True) -> User:
    async def _op(session: AsyncSession) -> User:
        user = User(
            email=email, password_hash=hash_password(STRONG_PASSWORD), role=role, is_active=active
        )
        session.add(user)
        await session.commit()
        return user

    return run_db(_op)


def fetch_all(sql: str, **params: Any) -> list[dict[str, Any]]:
    async def _op(session: AsyncSession) -> list[dict[str, Any]]:
        result = await session.execute(text(sql), params)
        return [dict(row._mapping) for row in result]

    return run_db(_op)


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> None:
    if not os.environ.get("NEXUSFLOW_TEST_DATABASE_URL"):
        return  # las pruebas de integración se omitirán (ver tests/conftest.py)
    env = {**os.environ, "NEXUSFLOW_DATABASE_URL": db_url()}
    subprocess.run(  # argumentos fijos, sin entrada externa
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
        timeout=60,
    )


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    if not os.environ.get("NEXUSFLOW_TEST_DATABASE_URL"):
        return

    async def _truncate(session: AsyncSession) -> None:
        await session.execute(
            text(
                "TRUNCATE audit_log, refresh_tokens, credentials, webhook_deliveries, "
                "webhook_subscriptions, webhook_triggers, step_attempts, step_runs, "
                "workflow_runs, workflow_versions, workflows, users "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()

    run_db(_truncate)
    SyncRedis.from_url(redis_url()).flushdb()


def login_headers(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    # https: la cookie de refresh es Secure y httpx no la enviaría por http.
    with TestClient(create_app(integration_settings()), base_url="https://testserver") as c:
        yield c


@pytest.fixture
def make_client() -> Iterator[Callable[..., TestClient]]:
    """Crea clientes con configuración a medida (límites de uso, registro, etc.)."""
    opened: list[TestClient] = []

    def _factory(**overrides: object) -> TestClient:
        c = TestClient(create_app(integration_settings(**overrides)), base_url="https://testserver")
        c.__enter__()
        opened.append(c)
        return c

    yield _factory
    for c in opened:
        c.__exit__(None, None, None)
