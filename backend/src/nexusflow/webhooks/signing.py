"""Firma HMAC de webhooks (entrantes y salientes), al estilo de Stripe.

Cabecera:  X-NexusFlow-Signature: t=<unix>,v1=<hex>
Firma:     v1 = HMAC-SHA256(secreto, f"{t}." + cuerpo_en_bytes)

- Se firma el cuerpo *exacto* en bytes, no un JSON re-serializado: cualquier cambio de un
  byte invalida la firma.
- El timestamp va dentro de lo firmado: un atacante no puede reutilizar una petición
  antigua cambiando `t`. Además se rechazan timestamps fuera de una ventana (anti-replay).
- Se admiten varias firmas `v1` (útil al rotar el secreto) y se comparan en tiempo
  constante (`hmac.compare_digest`).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

SIGNATURE_HEADER = "X-NexusFlow-Signature"
EVENT_ID_HEADER = "X-NexusFlow-Event-Id"
EVENT_TYPE_HEADER = "X-NexusFlow-Event-Type"
SECRET_PREFIX = "whsec_"  # noqa: S105  # prefijo, no un secreto


class SignatureError(ValueError):
    pass


def generate_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def compute(secret: str, timestamp: int, body: bytes) -> str:
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def sign(secret: str, body: bytes, timestamp: int | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    return f"t={ts},v1={compute(secret, ts, body)}"


def parse(header: str) -> tuple[int, list[str]]:
    timestamp: int | None = None
    signatures: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and value.isdigit():
            timestamp = int(value)
        elif key == "v1" and value:
            signatures.append(value)
    if timestamp is None or not signatures:
        raise SignatureError("Cabecera de firma mal formada")
    return timestamp, signatures


def verify(
    header: str | None,
    secret: str,
    body: bytes,
    *,
    tolerance_seconds: int,
    now: int | None = None,
) -> int:
    """Valida la firma y la ventana de tiempo. Devuelve el timestamp firmado."""
    if not header:
        raise SignatureError("Falta la cabecera de firma")
    timestamp, signatures = parse(header)
    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > tolerance_seconds:
        raise SignatureError("Firma fuera de la ventana de tiempo permitida")
    expected = compute(secret, timestamp, body)
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise SignatureError("Firma no válida")
    return timestamp
