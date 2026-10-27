from __future__ import annotations

import os

import pytest

from nexusflow.core.config import Settings

TEST_SECRET = "test" * 12  # valor ficticio de baja entropía: no es un secreto real
# Clave Fernet de pruebas: 32 bytes a cero codificados en base64 (no es un secreto real).
TEST_FERNET_KEY = "A" * 43 + "="
# URLs no enrutables para pruebas unitarias: nunca deben usarse para conectar.
UNIT_DB_URL = "postgresql+asyncpg://unit:unit@127.0.0.1:1/unit"
UNIT_REDIS_URL = "redis://127.0.0.1:1/0"


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "env": "test",
        "log_json": True,
        "database_url": UNIT_DB_URL,
        "redis_url": UNIT_REDIS_URL,
        "secret_key": TEST_SECRET,
        "credentials_encryption_keys": TEST_FERNET_KEY,
        "readiness_timeout_seconds": 0.5,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evita que variables NEXUSFLOW_* del equipo del desarrollador alteren las pruebas."""
    for key in list(os.environ):
        if key.startswith("NEXUSFLOW_") and not key.startswith("NEXUSFLOW_TEST_"):
            monkeypatch.delenv(key, raising=False)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Las pruebas de integración se omiten si no hay servicios configurados.

    En CI se define NEXUSFLOW_REQUIRE_INTEGRATION=1: entonces la falta de servicios es un
    error y no una omisión silenciosa, para no dar por buena una ejecución incompleta.
    """
    configured = bool(
        os.environ.get("NEXUSFLOW_TEST_DATABASE_URL") and os.environ.get("NEXUSFLOW_TEST_REDIS_URL")
    )
    if configured:
        return
    if os.environ.get("NEXUSFLOW_REQUIRE_INTEGRATION") == "1":
        raise pytest.UsageError(
            "NEXUSFLOW_REQUIRE_INTEGRATION=1 pero faltan NEXUSFLOW_TEST_DATABASE_URL "
            "o NEXUSFLOW_TEST_REDIS_URL"
        )
    skip = pytest.mark.skip(reason="servicios de integración no configurados")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
