"""Middleware ASGI puro: request_id, log de acceso y cabeceras de seguridad.

Se usa ASGI puro en lugar de BaseHTTPMiddleware para no romper respuestas en streaming
y para que los contextvars se propaguen correctamente.
"""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexusflow.core.logging import get_logger

REQUEST_ID_HEADER = "x-request-id"
# Un request_id recibido del cliente se acepta solo si es seguro de registrar en logs.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}

logger = get_logger("nexusflow.access")


def resolve_request_id(incoming: str | None) -> str:
    if incoming and _SAFE_REQUEST_ID.fullmatch(incoming):
        return incoming
    return uuid.uuid4().hex


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for name, value in scope.get("headers", []):
            if name == REQUEST_ID_HEADER.encode():
                incoming = value.decode("latin-1")
                break
        request_id = resolve_request_id(incoming)
        scope.setdefault("state", {})["request_id"] = request_id

        status_code = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                for key, value in SECURITY_HEADERS.items():
                    headers.setdefault(key, value)
            await send(message)

        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                duration_ms = round((time.perf_counter() - start) * 1000, 2)
                logger.info(
                    "http_request",
                    method=scope.get("method"),
                    path=scope.get("path"),
                    status=status_code,
                    duration_ms=duration_ms,
                )
