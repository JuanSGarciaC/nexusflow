"""API externa de prueba con fallos controlables, servida por uvicorn en un hilo.

No es un mock de httpx: es un servidor HTTP real en 127.0.0.1, así que los timeouts,
las conexiones y los códigos de estado son los de verdad.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

RECEIVER_SECRET = "whsec_receptor_de_pruebas_0123456789abcdef"  # solo pruebas


class PartnerState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.received: list[dict[str, Any]] = []
        self.receiver_failures = 0
        self.calls: Counter[str] = Counter()
        self.idempotency_keys: list[str] = []
        self.charges: dict[str, str] = {}
        # Simula que el servicio externo se recupera: ignora el parámetro ?delay=.
        self.healthy = False

    def reset(self) -> None:
        with self.lock:
            self.calls.clear()
            self.idempotency_keys.clear()
            self.charges.clear()
            self.healthy = False
            self.received.clear()
            self.receiver_failures = 0


STATE = PartnerState()


def _count(name: str, request: Request) -> int:
    with STATE.lock:
        STATE.calls[name] += 1
        key = request.headers.get("idempotency-key")
        if key:
            STATE.idempotency_keys.append(key)
        return STATE.calls[name]


async def customer(request: Request) -> Response:
    _count("customer", request)
    cid = request.path_params["cid"]
    return JSONResponse({"id": cid, "tier": "vip" if cid.startswith("vip") else "basic"})


async def charge(request: Request) -> Response:
    _count("charge", request)
    key = request.headers.get("idempotency-key", "")
    delay = 0.0 if STATE.healthy else float(request.query_params.get("delay", "0"))
    if delay:
        await asyncio.sleep(delay)
    with STATE.lock:
        existing = STATE.charges.get(key)
        if existing is None:
            existing = STATE.charges[key] = uuid.uuid4().hex
    return JSONResponse({"charge_id": existing, "idempotency_key": key}, status_code=201)


async def flaky(request: Request) -> Response:
    name = request.path_params["name"]
    attempt = _count(f"flaky:{name}", request)
    fail = int(request.query_params.get("fail", "1"))
    code = int(request.query_params.get("code", "503"))
    if attempt <= fail:
        headers = (
            {"Retry-After": request.query_params["retry_after"]}
            if "retry_after" in request.query_params
            else None
        )
        return JSONResponse(
            {"error": "temporal", "attempt": attempt}, status_code=code, headers=headers
        )
    return JSONResponse({"ok": True, "attempt": attempt})


async def slow(request: Request) -> Response:
    _count("slow", request)
    await asyncio.sleep(float(request.query_params.get("seconds", "3")))
    return JSONResponse({"slow": True})


async def big(request: Request) -> Response:
    _count("big", request)
    return PlainTextResponse("x" * int(request.query_params.get("bytes", "1000")))


async def status(request: Request) -> Response:
    _count("status", request)
    return JSONResponse(
        {"status": int(request.path_params["code"])}, status_code=int(request.path_params["code"])
    )


async def echo(request: Request) -> Response:
    _count("echo", request)
    body: Any = await request.json() if await request.body() else None
    return JSONResponse(
        {
            "method": request.method,
            "query": dict(request.query_params),
            "headers": {
                k: v
                for k, v in request.headers.items()
                if k.startswith("x-") or k in ("authorization", "idempotency-key")
            },
            "body": body,
        }
    )


async def receiver(request: Request) -> Response:
    """Receptor de webhooks: verifica la firma como lo haría un sistema real."""
    from nexusflow.webhooks import signing

    body = await request.body()
    try:
        signing.verify(
            request.headers.get(signing.SIGNATURE_HEADER),
            RECEIVER_SECRET,
            body,
            tolerance_seconds=300,
        )
    except signing.SignatureError:
        return JSONResponse({"error": "firma"}, status_code=401)
    with STATE.lock:
        if STATE.receiver_failures > 0:
            STATE.receiver_failures -= 1
            return JSONResponse({"error": "caído"}, status_code=500)
        STATE.received.append(
            {
                "event_id": request.headers.get(signing.EVENT_ID_HEADER),
                "event_type": request.headers.get(signing.EVENT_TYPE_HEADER),
                "payload": json.loads(body),
            }
        )
    return JSONResponse({"ok": True})


app = Starlette(
    routes=[
        Route("/customers/{cid}", customer),
        Route("/charges", charge, methods=["POST", "PUT"]),
        Route("/flaky/{name}", flaky, methods=["GET", "POST", "PUT"]),
        Route("/slow", slow, methods=["GET", "POST", "PUT"]),
        Route("/big", big),
        Route("/status/{code:int}", status, methods=["GET", "POST"]),
        Route("/echo", echo, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
        Route("/receiver", receiver, methods=["POST"]),
    ]
)


@contextmanager
def serve_partner() -> Iterator[str]:
    """Arranca el servidor en un puerto libre y devuelve su URL base."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("El servidor de prueba no arrancó")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
