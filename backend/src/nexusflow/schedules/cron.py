"""Expresiones cron de 5 campos evaluadas en una zona horaria IANA.

Se usa `cronsim` (sin dependencias): resuelve bien los cambios de hora. Si una hora no
existe (salto de primavera) el disparo se mueve a la siguiente que sí existe; si se repite
(retroceso de otoño) se dispara una sola vez.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cronsim import CronSim, CronSimError

MAX_EXPRESSION_LENGTH = 100


class InvalidScheduleError(ValueError):
    """La expresión o la zona horaria no son válidas."""


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise InvalidScheduleError(f"Zona horaria desconocida: {name!r}.") from exc


def next_fire(expression: str, timezone: str, after: datetime) -> datetime:
    """Primer instante (UTC) estrictamente posterior a `after` que cumple la expresión."""
    if after.tzinfo is None:
        raise ValueError("`after` debe llevar zona horaria")
    if len(expression) > MAX_EXPRESSION_LENGTH or len(expression.split()) != 5:
        raise InvalidScheduleError("La expresión cron debe tener exactamente 5 campos.")
    zone = _zone(timezone)
    try:
        iterator = CronSim(expression, after.astimezone(zone))
        return next(iterator).astimezone(UTC)
    except CronSimError as exc:
        raise InvalidScheduleError(f"Expresión cron no válida: {expression!r}.") from exc
    except StopIteration as exc:  # p. ej. 31 de febrero: nunca ocurre
        raise InvalidScheduleError("La expresión cron nunca se cumple.") from exc
