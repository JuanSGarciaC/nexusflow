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

    # --- Autenticación ---
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_ttl_days: int = Field(default=7, ge=1, le=90)
    jwt_issuer: str = "nexusflow"
    jwt_audience: str = "nexusflow-api"
    registration_enabled: bool = True
    # Secure por defecto. Los navegadores aceptan cookies Secure en http://localhost.
    cookie_secure: bool = True

    # --- Límites de uso (por ventana fija) ---
    login_rate_limit_per_ip: int = Field(default=20, ge=1, description="Intentos por minuto e IP")
    login_rate_limit_per_email: int = Field(
        default=10, ge=1, description="Intentos por 15 minutos y cuenta"
    )
    register_rate_limit_per_ip: int = Field(default=5, ge=1, description="Registros por hora e IP")

    # --- Motor de ejecución ---
    # Broker de Celery. Si no se indica, se usa redis_url.
    celery_broker_url: SecretStr | None = None
    run_max_duration_hours: int = Field(default=48, ge=1, le=24 * 30)
    step_output_max_bytes: int = Field(default=262_144, ge=1024, le=10 * 1_048_576)
    # Margen sobre el timeout del paso antes de dar por muerto al worker que lo ejecuta.
    step_lease_grace_seconds: int = Field(default=30, ge=5, le=600)
    # Esperas más largas no se encolan con countdown: las recoge el barrido periódico.
    max_dispatch_countdown_seconds: int = Field(default=900, ge=1, le=3000)
    sweep_interval_seconds: float = Field(default=15.0, ge=1, le=300)

    # --- Webhooks ---
    webhook_signature_tolerance_seconds: int = Field(default=300, ge=30, le=3600)
    webhook_delivery_max_attempts: int = Field(default=8, ge=1, le=20)
    webhook_delivery_timeout_seconds: int = Field(default=10, ge=1, le=60)

    # --- Salida HTTP (anti-SSRF) ---
    egress_allow_http: bool = False
    # Hosts que pueden resolver a IPs privadas. Solo para desarrollo (p. ej. mock-partner).
    egress_allowed_private_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Credenciales de integraciones ---
    # Claves Fernet separadas por comas. La primera cifra; todas descifran (rotación).
    # Generar con: nexusflow generate-key
    credentials_encryption_keys: SecretStr = Field(
        description="Una o más claves Fernet (base64 urlsafe de 32 bytes), separadas por comas",
    )

    # --- Observabilidad ---
    # Token Bearer que Prometheus envía al leer /metrics. Sin token, /metrics no existe (404):
    # las métricas revelan rutas, volumen y errores, así que no se publican por defecto.
    metrics_token: SecretStr | None = None

    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    docs_enabled: bool = True
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    # Límite del cuerpo de las peticiones: evita agotar memoria con JSON gigantes.
    max_request_body_bytes: int = Field(default=1_048_576, ge=1024, le=50 * 1_048_576)

    @field_validator("cors_origins", "egress_allowed_private_hosts", mode="before")
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

    @field_validator("credentials_encryption_keys")
    @classmethod
    def _check_fernet_keys(cls, value: SecretStr) -> SecretStr:
        from cryptography.fernet import Fernet

        keys = [k.strip() for k in value.get_secret_value().split(",") if k.strip()]
        if not keys:
            raise ValueError("credentials_encryption_keys necesita al menos una clave")
        for key in keys:
            try:
                Fernet(key.encode())
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    "credentials_encryption_keys contiene una clave Fernet no válida; "
                    "genera una con 'nexusflow generate-key'"
                ) from exc
        return value

    @field_validator("metrics_token")
    @classmethod
    def _check_metrics_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value():
            return None  # NEXUSFLOW_METRICS_TOKEN= (vacío) equivale a no definirlo
        if value is not None and len(value.get_secret_value()) < 24:
            raise ValueError("metrics_token debe tener al menos 24 caracteres")
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
        if not self.cookie_secure:
            raise ValueError("cookie_secure debe ser true en producción")
        if self.egress_allow_http or self.egress_allowed_private_hosts:
            raise ValueError(
                "egress_allow_http y egress_allowed_private_hosts son solo para desarrollo"
            )
        return self

    @property
    def broker_url(self) -> str:
        return (self.celery_broker_url or self.redis_url).get_secret_value()

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración cargada una sola vez por proceso."""
    # Los campos obligatorios vienen del entorno; si faltan, se lanza ValidationError aquí.
    return Settings()
