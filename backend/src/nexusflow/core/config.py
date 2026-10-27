"""Configuración de la aplicación a partir de variables de entorno.

Toda la configuración entra por el entorno (12-factor). Si falta un valor obligatorio
o es inseguro para el entorno declarado, la aplicación falla al arrancar en lugar de
funcionar con una configuración incorrecta.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

# Valores que aparecen en .env.example: nunca deben llegar a producción.
_PLACEHOLDER_MARKERS = ("change-me", "changeme", "example", "insecure")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEXUSFLOW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: Environment = "development"
    log_level: LogLevel = "INFO"
    log_json: bool = True

    database_url: SecretStr = Field(
        description="URL async de PostgreSQL, p. ej. postgresql+asyncpg://user:pass@host:5432/db",
    )
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=5, ge=0, le=50)

    redis_url: SecretStr = Field(description="URL de Redis, p. ej. redis://host:6379/0")

    secret_key: SecretStr = Field(
        description="Clave para firmar tokens. Mínimo 32 caracteres aleatorios.",
    )

    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    docs_enabled: bool = True
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        # Permite NEXUSFLOW_CORS_ORIGINS="http://a,http://b" además de JSON.
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("database_url debe usar el esquema postgresql+asyncpg://")
        return value

    @field_validator("redis_url")
    @classmethod
    def _check_redis_url(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith(("redis://", "rediss://")):
            raise ValueError("redis_url debe usar el esquema redis:// o rediss://")
        return value

    @field_validator("secret_key")
    @classmethod
    def _check_secret_length(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("secret_key debe tener al menos 32 caracteres")
        return value

    @model_validator(mode="after")
    def _check_production_safety(self) -> Settings:
        if self.env != "production":
            return self
        secret = self.secret_key.get_secret_value().lower()
        if any(marker in secret for marker in _PLACEHOLDER_MARKERS):
            raise ValueError("secret_key contiene un valor de ejemplo; genera uno real")
        if "*" in self.cors_origins:
            raise ValueError("cors_origins no puede contener '*' en producción")
        return self

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración cargada una sola vez por proceso."""
    # Los campos obligatorios vienen del entorno; si faltan, se lanza ValidationError aquí.
    return Settings()
