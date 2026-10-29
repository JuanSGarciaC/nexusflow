"""Datos de demostración: idempotentes, con los cuatro roles y sin secretos en claro."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nexusflow.demo import DEMO_USERS, seed_demo
from nexusflow.engine.dispatch import RecordingDispatcher
from tests.integration.conftest import db_url, integration_settings

pytestmark = pytest.mark.integration


async def fetch(sessions: async_sessionmaker[AsyncSession], sql: str) -> list[dict[str, Any]]:
    async with sessions() as s:
        return [dict(r._mapping) for r in await s.execute(text(sql))]


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_url())
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_seed_is_idempotent_and_creates_every_role(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MOCK_PARTNER_CRM_TOKEN", raising=False)
    dispatcher = RecordingDispatcher()
    first = await seed_demo(sessions, integration_settings(), dispatcher, runs_per_flow=3)
    assert first.password_generated is not None
    assert len(first.password_generated) >= 12
    assert sorted(first.users_created) == sorted(DEMO_USERS)
    assert len(first.workflows_created) == 2  # sin mock-partner no hay flujo de cobro
    assert len(first.schedules_created) == 2
    assert first.runs_started == 6  # 3 por flujo, rotando las entradas de ejemplo
    assert dispatcher.drain()  # se encolaron los primeros pasos

    second = await seed_demo(
        sessions, integration_settings(), dispatcher, runs_per_flow=0, password="otra-clave-larga-1"
    )
    assert second.users_created == []
    assert second.workflows_created == []
    assert second.schedules_created == []  # no se duplica
    assert second.password_generated is None

    roles = {r["email"]: r["role"] for r in await fetch(sessions, "SELECT email, role FROM users")}
    assert roles == {email: role.value for email, role in DEMO_USERS.items()}
    published = await fetch(
        sessions,
        "SELECT w.name FROM workflows w JOIN workflow_versions v ON v.workflow_id = w.id "
        "WHERE v.published_at IS NOT NULL",
    )
    assert len(published) == 2
    assert len(await fetch(sessions, "SELECT id FROM workflow_schedules")) == 2


async def test_seed_with_partner_creates_encrypted_credentials(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOCK_PARTNER_CRM_TOKEN", "crm-token-de-prueba-123")
    monkeypatch.setenv("MOCK_PARTNER_PAYMENTS_KEY", "clave-pagos-de-prueba-456")
    report = await seed_demo(
        sessions,
        integration_settings(),
        RecordingDispatcher(),
        runs_per_flow=1,
        password="demo-clave-segura-2026",
    )
    assert sorted(report.credentials_created) == ["demo_crm", "demo_pagos"]
    assert "Demo · Cobro a cliente VIP" in report.workflows_created
    stored = str(await fetch(sessions, "SELECT * FROM credentials"))
    assert "crm-token-de-prueba-123" not in stored
    assert "clave-pagos-de-prueba-456" not in stored


async def test_seed_rejects_weak_demo_password(sessions: async_sessionmaker[AsyncSession]) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await seed_demo(
            sessions,
            integration_settings(),
            RecordingDispatcher(),
            runs_per_flow=0,
            password="corta",
        )
    assert await fetch(sessions, "SELECT id FROM users") == []
