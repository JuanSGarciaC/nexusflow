"""Concurrencia real contra PostgreSQL: dos operaciones simultáneas sobre el mismo recurso."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nexusflow.audit.service import RequestContext
from nexusflow.auth import service
from nexusflow.auth.models import RefreshToken
from nexusflow.auth.schemas import LoginRequest, RegisterRequest
from nexusflow.core.errors import ConflictError, UnauthorizedError
from tests.integration.conftest import STRONG_PASSWORD, db_url, integration_settings

pytestmark = pytest.mark.integration

CTX = RequestContext(ip_address="127.0.0.1", request_id="concurrency-test")


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_url(), pool_size=5)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_concurrent_refresh_of_same_token_succeeds_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = integration_settings()
    async with session_factory() as s:
        await service.register_user(
            s,
            settings,
            RegisterRequest(email="race@example.com", password=SecretStr(STRONG_PASSWORD)),
            CTX,
        )
    async with session_factory() as s:
        issued = await service.authenticate(
            s,
            settings,
            LoginRequest(email="race@example.com", password=SecretStr(STRONG_PASSWORD)),
            CTX,
        )

    async def attempt() -> service.IssuedTokens:
        async with session_factory() as s:
            return await service.rotate_refresh_token(s, settings, issued.refresh_token, CTX)

    results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)
    successes = [r for r in results if isinstance(r, service.IssuedTokens)]
    failures = [r for r in results if isinstance(r, UnauthorizedError)]
    assert len(successes) == 1, results
    assert len(failures) == 1, results

    # El segundo canje se interpretó como reutilización: la familia completa está revocada.
    async with session_factory() as s:
        active = await s.scalars(select(RefreshToken).where(RefreshToken.revoked_at.is_(None)))
        assert list(active) == []


async def test_concurrent_registration_with_same_email(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = integration_settings()

    async def attempt() -> object:
        async with session_factory() as s:
            return await service.register_user(
                s,
                settings,
                RegisterRequest(email="twice@example.com", password=SecretStr(STRONG_PASSWORD)),
                CTX,
            )

    results = await asyncio.gather(*(attempt() for _ in range(5)), return_exceptions=True)
    conflicts = [r for r in results if isinstance(r, ConflictError)]
    assert len(conflicts) == 4, results
    assert sum(1 for r in results if not isinstance(r, Exception)) == 1
