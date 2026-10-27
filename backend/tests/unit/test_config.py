from __future__ import annotations

import pytest
from pydantic import ValidationError

from nexusflow.core.config import Settings
from tests.conftest import TEST_FERNET_KEY, TEST_SECRET, make_settings


def test_valid_settings_load() -> None:
    settings = make_settings()
    assert settings.env == "test"
    assert not settings.is_production


def test_missing_required_values_fail_fast() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    missing = {err["loc"][0] for err in exc_info.value.errors()}
    assert {"database_url", "redis_url", "secret_key", "credentials_encryption_keys"} <= missing


def test_short_secret_key_rejected() -> None:
    with pytest.raises(ValidationError, match="32 caracteres"):
        make_settings(secret_key="short")


@pytest.mark.parametrize(
    "url",
    ["postgresql://u:p@h/db", "sqlite:///x.db", "mysql+asyncmy://u:p@h/db"],
)
def test_database_url_requires_asyncpg(url: str) -> None:
    with pytest.raises(ValidationError, match="postgresql\\+asyncpg"):
        make_settings(database_url=url)


def test_redis_url_scheme_validated() -> None:
    with pytest.raises(ValidationError, match="redis://"):
        make_settings(redis_url="http://localhost:6379")


def test_production_rejects_placeholder_secret() -> None:
    with pytest.raises(ValidationError, match="valor de ejemplo"):
        make_settings(env="production", secret_key="change-me-" + "x" * 40)


def test_production_rejects_wildcard_cors() -> None:
    with pytest.raises(ValidationError, match="cors_origins"):
        make_settings(env="production", secret_key="Zq" * 20, cors_origins=["*"])


def test_production_accepts_strong_config() -> None:
    settings = make_settings(env="production", secret_key="Zq" * 20)
    assert settings.is_production


def test_cors_origins_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXUSFLOW_DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/db")
    monkeypatch.setenv("NEXUSFLOW_REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv("NEXUSFLOW_SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("NEXUSFLOW_CREDENTIALS_ENCRYPTION_KEYS", TEST_FERNET_KEY)
    monkeypatch.setenv("NEXUSFLOW_CORS_ORIGINS", "http://localhost:5173, http://127.0.0.1:5173")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_secrets_not_exposed_in_repr() -> None:
    settings = make_settings()
    rendered = repr(settings) + str(settings.model_dump())
    assert TEST_SECRET not in rendered
    assert "unit:unit" not in rendered


@pytest.mark.parametrize("keys", ["no-es-una-clave", "", " , "])
def test_invalid_fernet_keys_rejected(keys: str) -> None:
    with pytest.raises(ValidationError, match="credentials_encryption_keys"):
        make_settings(credentials_encryption_keys=keys)


def test_production_rejects_egress_relaxations() -> None:
    with pytest.raises(ValidationError, match="solo para desarrollo"):
        make_settings(env="production", secret_key="Zq" * 20, egress_allow_http=True)
