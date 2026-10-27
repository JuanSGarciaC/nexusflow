"""Firma HMAC de webhooks y backoff de entregas."""

from __future__ import annotations

import random

import pytest
from pydantic import ValidationError

from nexusflow.webhooks import signing
from nexusflow.webhooks.delivery import delivery_backoff
from nexusflow.webhooks.schemas import SubscriptionCreate

SECRET = "whsec_" + "a" * 40
BODY = b'{"order":"A-1","amount":10}'
NOW = 1_800_000_000


def test_sign_and_verify() -> None:
    header = signing.sign(SECRET, BODY, timestamp=NOW)
    assert header.startswith(f"t={NOW},v1=")
    assert signing.verify(header, SECRET, BODY, tolerance_seconds=300, now=NOW) == NOW


@pytest.mark.parametrize(
    ("body", "secret", "now", "match"),
    [
        (BODY + b" ", SECRET, NOW, "no válida"),  # un byte de más
        (BODY.replace(b"10", b"99"), SECRET, NOW, "no válida"),  # importe manipulado
        (BODY, "whsec_otro" + "b" * 40, NOW, "no válida"),
        (BODY, SECRET, NOW + 301, "ventana"),  # replay tardío
        (BODY, SECRET, NOW - 301, "ventana"),  # timestamp del futuro
    ],
)
def test_rejections(body: bytes, secret: str, now: int, match: str) -> None:
    header = signing.sign(SECRET, BODY, timestamp=NOW)
    with pytest.raises(signing.SignatureError, match=match):
        signing.verify(header, secret, body, tolerance_seconds=300, now=now)


def test_timestamp_is_covered_by_signature() -> None:
    # Cambiar t para "rejuvenecer" una petición capturada invalida la firma.
    header = signing.sign(SECRET, BODY, timestamp=NOW - 1000)
    forged = header.replace(f"t={NOW - 1000}", f"t={NOW}")
    with pytest.raises(signing.SignatureError, match="no válida"):
        signing.verify(forged, SECRET, BODY, tolerance_seconds=300, now=NOW)


@pytest.mark.parametrize("header", [None, "", "v1=abc", "t=123", "t=abc,v1=x", "basura"])
def test_malformed_headers(header: str | None) -> None:
    with pytest.raises(signing.SignatureError):
        signing.verify(header, SECRET, BODY, tolerance_seconds=300, now=NOW)


def test_multiple_signatures_accepted_during_rotation() -> None:
    old = signing.compute("whsec_viejo" + "c" * 40, NOW, BODY)
    new = signing.compute(SECRET, NOW, BODY)
    header = f"t={NOW},v1={old},v1={new}"
    assert signing.verify(header, SECRET, BODY, tolerance_seconds=300, now=NOW) == NOW


def test_generated_secrets_are_unique_and_prefixed() -> None:
    a, b = signing.generate_secret(), signing.generate_secret()
    assert a != b
    assert a.startswith("whsec_")
    assert len(a) > 40


def test_delivery_backoff_schedule() -> None:
    rng = random.Random(7)  # noqa: S311  # determinista a propósito
    for attempt, base in [(1, 30), (2, 60), (3, 120), (8, 3600), (20, 3600)]:
        delay = delivery_backoff(attempt, rng)
        assert base * 0.8 <= delay <= base * 1.2


def test_subscription_event_validation() -> None:
    ok = SubscriptionCreate(
        name="x", url="https://hooks.example.com", events=["run.failed", "run.failed"]
    )
    assert ok.events == ["run.failed"]
    with pytest.raises(ValidationError, match="desconocidos"):
        SubscriptionCreate(name="x", url="https://hooks.example.com", events=["run.borrado"])
    with pytest.raises(ValidationError):
        SubscriptionCreate(name="x", url="https://hooks.example.com", events=["ping"])
