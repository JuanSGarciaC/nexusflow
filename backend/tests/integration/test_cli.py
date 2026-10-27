"""Comando `nexusflow create-admin` contra la base de datos real."""

from __future__ import annotations

import asyncio

import pytest

from nexusflow.cli import create_admin
from nexusflow.core.config import get_settings
from tests.integration.conftest import STRONG_PASSWORD, db_url, fetch_all, redis_url

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cli_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXUSFLOW_DATABASE_URL", db_url())
    monkeypatch.setenv("NEXUSFLOW_REDIS_URL", redis_url())
    monkeypatch.setenv("NEXUSFLOW_SECRET_KEY", "cli" * 12)
    get_settings.cache_clear()


def test_create_admin_creates_audited_admin() -> None:
    assert asyncio.run(create_admin("Root@Example.com", STRONG_PASSWORD)) == 0
    users = fetch_all("SELECT email, role FROM users")
    assert users == [{"email": "root@example.com", "role": "admin"}]
    actions = [r["action"] for r in fetch_all("SELECT action FROM audit_log")]
    assert actions == ["user.created_by_cli"]


def test_create_admin_rejects_duplicates_and_weak_passwords(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert asyncio.run(create_admin("root@example.com", STRONG_PASSWORD)) == 0
    assert asyncio.run(create_admin("ROOT@example.com", STRONG_PASSWORD)) == 1
    assert asyncio.run(create_admin("otro@example.com", "corta")) == 2
    errors = capsys.readouterr().err
    assert "ya existe" in errors
    assert "entre 12 y 128" in errors
    assert len(fetch_all("SELECT id FROM users")) == 1
