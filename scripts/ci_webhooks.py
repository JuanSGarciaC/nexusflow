"""Smoke test de CI: webhook entrante firmado y webhook saliente verificado por mock-partner.

Uso: python scripts/ci_webhooks.py <access_token>
Requiere MOCK_PARTNER_WEBHOOK_SECRET en el entorno. Solo biblioteca estándar.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

API = "http://127.0.0.1:8000/api/v1"
BASE = "http://127.0.0.1:8000"
PARTNER = "http://127.0.0.1:8080"

DEFINITION = {
    "inputs": [{"name": "order_id", "type": "string"}],
    "start": "ack",
    "steps": [
        {"id": "ack", "type": "transform", "config": {"output": {"ack": "{{ input.order_id }}"}}}
    ],
}


def call(
    method: str,
    url: str,
    token: str | None = None,
    body: Any = None,
    raw: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(url, data=data, method=method)  # noqa: S310  # URL fija
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def sign(secret: str, body: bytes) -> str:
    ts = int(time.time())
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def main(token: str) -> int:
    failures: list[str] = []
    _, workflow = call(
        "POST", f"{API}/workflows", token, {"name": "ci-webhooks", "definition": DEFINITION}
    )
    call("POST", f"{API}/workflows/{workflow['id']}/publish", token, {"expected_revision": 1})

    status, _ = call(
        "POST",
        f"{API}/webhook-subscriptions",
        token,
        {
            "name": "mock-partner",
            "url": "http://mock-partner:8080/webhooks/nexusflow",
            "events": ["run.succeeded"],
            "workflow_id": workflow["id"],
            "secret": os.environ["MOCK_PARTNER_WEBHOOK_SECRET"],
        },
    )
    if status != 201:
        failures.append(f"crear suscripción devolvió {status}")

    _, trigger = call("POST", f"{API}/workflows/{workflow['id']}/triggers", token, {"name": "ci"})
    body = json.dumps({"order_id": "WH-1", "extra": True}).encode()
    hook = f"{BASE}{trigger['url_path']}"

    bad, _ = call("POST", hook, raw=body, headers={"X-NexusFlow-Signature": "t=1,v1=00"})
    if bad != 401:
        failures.append(f"firma inválida devolvió {bad} (se esperaba 401)")

    headers = {
        "X-NexusFlow-Signature": sign(trigger["secret"], body),
        "X-NexusFlow-Event-Id": "ci-evt-1",
    }
    first, accepted = call("POST", hook, raw=body, headers=headers)
    again, repeated = call("POST", hook, raw=body, headers=headers)
    if (first, again) != (202, 200) or repeated["run_id"] != accepted["run_id"]:
        failures.append(f"deduplicación: {first}/{again}")

    received: dict[str, Any] = {"count": 0, "items": []}
    for _ in range(40):  # la entrega puede tardar hasta un barrido si el encolado falló
        _, received = call("GET", f"{PARTNER}/webhooks/received")
        if received["count"]:
            break
        time.sleep(1)
    print("eventos recibidos por mock-partner:", received["count"])
    if received["count"] != 1:
        failures.append("mock-partner no recibió exactamente un evento firmado")
    elif received["items"][0]["data"]["output"] != {"ack": "WH-1"}:
        failures.append("el evento recibido no corresponde a la ejecución")

    for message in failures:
        print("FALLO:", message)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
