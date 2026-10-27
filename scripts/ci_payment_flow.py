"""Smoke test de CI: flujo con credenciales cifradas contra mock-partner.

Uso: python scripts/ci_payment_flow.py <access_token>
Solo usa la biblioteca estándar para poder ejecutarse en el runner sin instalar nada.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from typing import Any

API = "http://127.0.0.1:8000/api/v1"
PARTNER = "http://127.0.0.1:8080"

DEFINITION = {
    "inputs": [{"name": "customer_id", "type": "string"}, {"name": "amount", "type": "number"}],
    "start": "lookup",
    "steps": [
        {
            "id": "lookup",
            "type": "http_request",
            "config": {
                "method": "GET",
                "url": "http://mock-partner:8080/crm/customers/{{ input.customer_id }}",
                "credential": "crm",
            },
            "retry": {"max_attempts": 3, "initial_backoff_seconds": 0.5},
            "next": "is_vip",
        },
        {
            "id": "is_vip",
            "type": "condition",
            "config": {
                "when": {"field": "steps.lookup.output.body.tier", "op": "eq", "value": "vip"}
            },
            "on_true": "charge",
        },
        {
            "id": "charge",
            "type": "http_request",
            "config": {
                "method": "POST",
                # Las dos primeras llamadas fallan con 503: se ven los reintentos.
                "url": "http://mock-partner:8080/payments/charges?fail=2&key=ci",
                "credential": "pagos",
                "body": {"customer_id": "{{ input.customer_id }}", "amount": "{{ input.amount }}"},
            },
            # La pasarela deduplica por Idempotency-Key: reintentar es seguro.
            "side_effect": "idempotent",
            "retry": {"max_attempts": 4, "initial_backoff_seconds": 0.5},
            "next": "receipt",
        },
        {
            "id": "receipt",
            "type": "transform",
            "config": {"output": {"charge_id": "{{ steps.charge.output.body.id }}"}},
        },
    ],
}


def call(method: str, url: str, token: str | None = None, body: Any = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)  # noqa: S310  # URL fija
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.loads(response.read() or b"null")


def main(token: str) -> int:
    workflow = call(
        "POST", f"{API}/workflows", token, {"name": "ci-pagos", "definition": DEFINITION}
    )
    published = call(
        "POST", f"{API}/workflows/{workflow['id']}/publish", token, {"expected_revision": 1}
    )
    if published["issues"]:
        print("Incidencias:", published["issues"])
        return 1
    run = call(
        "POST",
        f"{API}/workflows/{workflow['id']}/runs",
        token,
        {"input": {"customer_id": "c-100", "amount": 49.9}},
    )
    for _ in range(60):
        run = call("GET", f"{API}/runs/{run['id']}", token)
        print("estado:", run["status"])
        if run["status"] not in ("pending", "running"):
            break
        time.sleep(1)

    if run["status"] != "succeeded":
        print(json.dumps(run, indent=2))
        return 1
    charge = next(s for s in run["steps"] if s["step_id"] == "charge")
    charges = call("GET", f"{PARTNER}/payments/charges")
    print(
        "intentos del cobro:", charge["attempt_count"], "| cargos en la pasarela:", charges["count"]
    )
    # Comprobaciones explícitas (no assert: con python -O se eliminarían).
    checks = {
        "se esperaban 2 fallos 503 y un éxito": charge["attempt_count"] == 3,
        "el reintento no debe duplicar el cargo": charges["count"] == 1,
        "la salida debe ser el cargo creado": (
            charges["count"] == 1 and run["output"]["charge_id"] == charges["items"][0]["id"]
        ),
    }
    failed = [message for message, ok in checks.items() if not ok]
    for message in failed:
        print("FALLO:", message)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
