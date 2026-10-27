"""Logs estructurados con structlog.

- Una sola salida para los logs propios y los de librerías (uvicorn, sqlalchemy).
- JSON en producción y CI; formato legible en desarrollo.
- Redacción automática de claves sensibles para que un secreto nunca llegue a los logs.
- El request_id se añade a cada línea mediante contextvars.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog
from structlog.typing import EventDict, Processor

REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "credential",
    "signature",
)


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and _is_sensitive(k) else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(_redact(v) for v in value)
    return value


def redact_sensitive(_logger: Any, _method_name: str, event_dict: EventDict) -> EventDict:
    """Procesador de structlog que oculta valores cuyas claves parecen secretas."""
    redacted: MutableMapping[str, Any] = {}
    for key, value in event_dict.items():
        redacted[key] = REDACTED if _is_sensitive(key) else _redact(value)
    return dict(redacted)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive,
    ]

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn registra sus propios handlers; los redirigimos al root para un único formato.
    for name in ("uvicorn", "uvicorn.error"):
        lib_logger = logging.getLogger(name)
        lib_logger.handlers.clear()
        lib_logger.propagate = True
    # El access log de uvicorn se sustituye por el del middleware (incluye request_id).
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name)
