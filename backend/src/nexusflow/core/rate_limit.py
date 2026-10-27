"""Limitador de uso de ventana fija sobre Redis.

Ventana fija (contador con caducidad) en lugar de ventana deslizante: es atómica con un
solo script, barata y suficiente para frenar fuerza bruta. Su limitación conocida es que
permite hasta 2x el límite en el borde entre dos ventanas, algo aceptable aquí.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from nexusflow.core.errors import AppError, ServiceUnavailableError
from nexusflow.core.logging import get_logger

logger = get_logger(__name__)

# INCR + EXPIRE en un único paso atómico. Devuelve {contador, ttl_restante}.
_FIXED_WINDOW = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""


class RateLimitExceededError(AppError):
    status_code = 429
    code = "rate-limited"
    title = "Demasiadas solicitudes"

    def __init__(self, retry_after: int) -> None:
        super().__init__("Has superado el límite de intentos. Inténtalo más tarde.")
        self.retry_after = retry_after

    def response_headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after)}


@dataclass(frozen=True, slots=True)
class RateLimit:
    scope: str
    limit: int
    window_seconds: int


def _key(scope: str, identifier: str) -> str:
    # Se guarda un hash del identificador (IP o email): Redis no almacena datos personales.
    digest = hashlib.sha256(identifier.encode()).hexdigest()[:32]
    return f"ratelimit:{scope}:{digest}"


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._script = redis.register_script(_FIXED_WINDOW)

    async def hit(self, rule: RateLimit, identifier: str) -> None:
        """Cuenta un intento. Lanza RateLimitExceededError si se supera el límite.

        Falla cerrado: si Redis no responde, se rechaza la petición (503). En endpoints de
        autenticación es preferible negar el servicio a permitir fuerza bruta sin límite.
        """
        try:
            count, ttl = await self._script(
                keys=[_key(rule.scope, identifier)], args=[rule.window_seconds]
            )
        except RedisError as exc:
            logger.error(
                "rate_limiter_unavailable", scope=rule.scope, error_type=type(exc).__name__
            )
            raise ServiceUnavailableError("Servicio no disponible temporalmente.") from exc
        if int(count) > rule.limit:
            retry_after = max(int(ttl), 1)
            logger.warning("rate_limit_exceeded", scope=rule.scope, retry_after=retry_after)
            raise RateLimitExceededError(retry_after)

    async def reset(self, rule: RateLimit, identifier: str) -> None:
        try:
            await self._redis.delete(_key(rule.scope, identifier))
        except RedisError:
            logger.warning("rate_limiter_reset_failed", scope=rule.scope)
