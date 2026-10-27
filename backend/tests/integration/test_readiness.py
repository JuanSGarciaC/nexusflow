"""Integración contra PostgreSQL y Redis reales (sin mocks)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nexusflow.main import create_app
from tests.conftest import make_settings

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _db_url() -> str:
    return os.environ["NEXUSFLOW_TEST_DATABASE_URL"]


def _redis_url() -> str:
    return os.environ["NEXUSFLOW_TEST_REDIS_URL"]


def test_ready_ok_with_real_services() -> None:
    app = create_app(make_settings(database_url=_db_url(), redis_url=_redis_url()))
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["redis"]["status"] == "ok"


def test_ready_degrades_when_only_redis_is_down() -> None:
    app = create_app(make_settings(database_url=_db_url(), redis_url="redis://127.0.0.1:1/0"))
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["database"]["status"] == "ok"
    assert checks["redis"]["status"] == "error"


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "NEXUSFLOW_DATABASE_URL": _db_url()}
    return subprocess.run(  # noqa: S603  # argumentos fijos, sin entrada externa
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_migrations_upgrade_and_downgrade() -> None:
    for args in (("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head")):
        result = _alembic(*args)
        assert result.returncode == 0, result.stderr


def test_models_match_migrations() -> None:
    """Falla si alguien cambia un modelo y olvida generar la migración."""
    assert _alembic("upgrade", "head").returncode == 0
    result = _alembic("check")
    assert result.returncode == 0, result.stdout + result.stderr
