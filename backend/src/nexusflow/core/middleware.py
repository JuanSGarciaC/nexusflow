"""Middleware ASGI puro: request_id, log de acceso y cabeceras de seguridad.

Se usa ASGI puro en lugar de BaseHTTPMiddleware para no romper respuestas en streaming
y para que los contextvars se propaguen correctamente.
"""

from __future__ import annotations

import json
import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexusflow.core.errors import PROBLEM_JSON, PROBLEM_TYPE_BASE, AppError
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


class RequestTooLargeError(AppError):
    status_code = 413
    code = "payload-too-large"
    title = "El cuerpo de la petición es demasiado grande"


class BodySizeLimitMiddleware:
    """Rechaza cuerpos mayores que `max_bytes`.

    - Con Content-Length: se responde 413 sin leer el cuerpo.
    - Sin Content-Length (chunked): se cuentan los bytes al recibirlos y se corta en cuanto
      se supera el límite, antes de que la aplicación lo cargue entero en memoria.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = self.max_bytes + 1
                if declared > self.max_bytes:
                    await self._reject(scope, send)
                    return
                break

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestTooLargeError(f"El máximo permitido es {self.max_bytes} bytes.")
            return message

        await self.app(scope, limited_receive, send)

    async def _reject(self, scope: Scope, send: Send) -> None:
        request_id = scope.get("state", {}).get("request_id")
        body = {
            "type": f"{PROBLEM_TYPE_BASE}{RequestTooLargeError.code}",
            "title": RequestTooLargeError.title,
            "status": 413,
            "detail": f"El máximo permitido es {self.max_bytes} bytes.",
            "instance": scope.get("path", ""),
        }
        if request_id:
            body["request_id"] = request_id
        payload = json.dumps(body).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", PROBLEM_JSON.encode()),
                    (b"content-length", str(len(payload)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})
