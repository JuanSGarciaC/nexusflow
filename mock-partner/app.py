"""API externa simulada para demos y pruebas de NexusFlow.

NO es parte del producto: representa los sistemas de terceros a los que llaman los
flujos. Permite inyectar fallos para ver el motor en acción.

Endpoints:
- GET  /health
- GET  /crm/customers/{id}           Bearer MOCK_PARTNER_CRM_TOKEN
- POST /payments/charges             cabecera X-Api-Key = MOCK_PARTNER_PAYMENTS_KEY;
                                     respeta Idempotency-Key (misma clave → mismo cargo)
- GET  /payments/charges             cargos realizados (para comprobar duplicados)
- POST /webhooks/nexusflow           receptor de webhooks salientes de NexusFlow; verifica
                                     la firma con MOCK_PARTNER_WEBHOOK_SECRET y deduplica
                                     por X-NexusFlow-Event-Id
- GET  /webhooks/received            eventos recibidos

Fallos inyectables en cualquier endpoint de negocio:
- ?delay=<segundos>      responde tarde (provoca timeouts)
- ?fail=<n>&key=<k>      las primeras n llamadas con esa clave devuelven ?status (503)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from collections import Counter
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

CRM_TOKEN = os.environ.get("MOCK_PARTNER_CRM_TOKEN", "")
PAYMENTS_KEY = os.environ.get("MOCK_PARTNER_PAYMENTS_KEY", "")
WEBHOOK_SECRET = os.environ.get("MOCK_PARTNER_WEBHOOK_SECRET", "")
SIGNATURE_TOLERANCE_SECONDS = 300

_failures: Counter[str] = Counter()
_charges: dict[str, dict[str, Any]] = {}
_received: dict[str, dict[str, Any]] = {}
_lock = asyncio.Lock()

CUSTOMERS = {
    "c-100": {"id": "c-100", "name": "Ana Torres", "tier": "vip", "balance": 1520.5},
    "c-200": {"id": "c-200", "name": "Luis Pérez", "tier": "basic", "balance": 80.0},
    "c-300": {"id": "c-300", "name": "Marta Ruiz", "tier": "vip", "balance": 0.0},
}


def _secure_equals(given: str, expected: str) -> bool:
    # Comparación en tiempo constante; un secreto vacío nunca autentica.
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


async def _faults(request: Request) -> Response | None:
    delay = float(request.query_params.get("delay", "0"))
    if 0 < delay <= 120:
        await asyncio.sleep(delay)
    fail = int(request.query_params.get("fail", "0"))
    if fail:
        key = request.query_params.get("key", request.url.path)
        async with _lock:
            _failures[key] += 1
            attempt = _failures[key]
        if attempt <= fail:
            status = int(request.query_params.get("status", "503"))
            return JSONResponse(
                {"error": "fallo inyectado", "attempt": attempt}, status_code=status
            )
    return None


async def health(_: Request) -> Response:
    return JSONResponse({"status": "ok"})


async def customer(request: Request) -> Response:
    auth = request.headers.get("authorization", "")
    if not _secure_equals(auth.removeprefix("Bearer "), CRM_TOKEN) or not auth.startswith(
        "Bearer "
    ):
        return JSONResponse({"error": "no autorizado"}, status_code=401)
    if (fault := await _faults(request)) is not None:
        return fault
    found = CUSTOMERS.get(request.path_params["cid"])
    if found is None:
        return JSONResponse({"error": "cliente no encontrado"}, status_code=404)
    return JSONResponse(found)


async def create_charge(request: Request) -> Response:
    if not _secure_equals(request.headers.get("x-api-key", ""), PAYMENTS_KEY):
        return JSONResponse({"error": "no autorizado"}, status_code=401)
    if (fault := await _faults(request)) is not None:
        return fault
    try:
        body = await request.json()
        amount = float(body["amount"])
        customer_id = str(body["customer_id"])
    except (ValueError, KeyError, TypeError):
        return JSONResponse({"error": "se requieren amount y customer_id"}, status_code=400)
    key = request.headers.get("idempotency-key")
    async with _lock:
        if key and key in _charges:
            return JSONResponse({**_charges[key], "replayed": True}, status_code=200)
        charge = {
            "id": f"ch_{uuid.uuid4().hex[:12]}",
            "amount": amount,
            "customer_id": customer_id,
            "idempotency_key": key,
        }
        _charges[key or charge["id"]] = charge
    return JSONResponse(charge, status_code=201)


async def list_charges(_: Request) -> Response:
    return JSONResponse({"count": len(_charges), "items": list(_charges.values())})


def _valid_signature(header: str, body: bytes) -> bool:
    """Verificación de referencia, tal como la implementaría un receptor real."""
    pairs = [part.strip().split("=", 1) for part in header.split(",") if "=" in part]
    timestamp = next((value for key, value in pairs if key == "t"), "")
    candidates = [value for key, value in pairs if key == "v1"]
    if not timestamp.isdigit() or not candidates or not WEBHOOK_SECRET:
        return False
    if abs(time.time() - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
        return False  # anti-replay
    expected = hmac.new(
        WEBHOOK_SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in candidates)


async def receive_webhook(request: Request) -> Response:
    body = await request.body()
    if not _valid_signature(request.headers.get("x-nexusflow-signature", ""), body):
        return JSONResponse({"error": "firma no válida"}, status_code=401)
    event_id = request.headers.get("x-nexusflow-event-id", "")
    async with _lock:
        duplicate = event_id in _received
        if not duplicate:
            _received[event_id] = json.loads(body)
    return JSONResponse({"ok": True, "duplicate": duplicate})


async def list_received(_: Request) -> Response:
    return JSONResponse({"count": len(_received), "items": list(_received.values())})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/crm/customers/{cid}", customer),
        Route("/payments/charges", create_charge, methods=["POST"]),
        Route("/payments/charges", list_charges, methods=["GET"]),
        Route("/webhooks/nexusflow", receive_webhook, methods=["POST"]),
        Route("/webhooks/received", list_received, methods=["GET"]),
    ]
)
