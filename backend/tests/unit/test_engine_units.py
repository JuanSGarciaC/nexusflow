"""Piezas puras del motor: anti-SSRF, backoff, clasificación, estados y decisiones."""

from __future__ import annotations

import random
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest

from nexusflow.db.types import utcnow
from nexusflow.engine.executors import StepError
from nexusflow.engine.http_client import (
    EgressPolicy,
    FailureKind,
    OutboundHttpError,
    classify_status,
    parse_retry_after,
    resolve_destination,
)
from nexusflow.engine.inputs import InvalidRunInputError, validate_run_input
from nexusflow.engine.models import (
    InvalidTransitionError,
    RunStatus,
    StepRun,
    StepStatus,
    WorkflowRun,
    assert_run_transition,
    assert_step_transition,
)
from nexusflow.engine.planning import compute_backoff
from nexusflow.engine.runner import apply_failure
from nexusflow.workflows.dsl import InputField, RetryPolicy, WorkflowDefinition
from tests.conftest import make_settings

# ------------------------------------------------------------------ anti-SSRF

STRICT = EgressPolicy()


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/",
        "https://10.0.0.5/admin",
        "https://192.168.1.1/",
        "https://172.16.0.1/",
        "https://169.254.169.254/latest/meta-data/",  # metadatos cloud
        "https://[::1]/",
        "https://[::ffff:127.0.0.1]/",  # IPv4 encapsulada en IPv6
        "https://100.64.0.1/",  # CGNAT
        "https://0.0.0.0/",
        "https://localhost/",
    ],
)
async def test_private_destinations_blocked(url: str) -> None:
    with pytest.raises(OutboundHttpError) as exc_info:
        await resolve_destination(httpx.URL(url), STRICT)
    assert exc_info.value.kind == FailureKind.PERMANENT
    assert exc_info.value.error_class == "egress_blocked"


async def test_public_ip_allowed() -> None:
    ip = await resolve_destination(httpx.URL("https://93.184.216.34/"), STRICT)
    assert str(ip) == "93.184.216.34"


@pytest.mark.parametrize(
    ("url", "error_class"),
    [
        ("http://93.184.216.34/", "egress_blocked"),  # http no permitido por defecto
        ("https://user:pass@93.184.216.34/", "egress_blocked"),
        ("ftp://93.184.216.34/", "egress_blocked"),
    ],
)
async def test_scheme_and_userinfo_rules(url: str, error_class: str) -> None:
    with pytest.raises(OutboundHttpError) as exc_info:
        await resolve_destination(httpx.URL(url), STRICT)
    assert exc_info.value.error_class == error_class


async def test_explicitly_trusted_private_host() -> None:
    policy = EgressPolicy(allow_http=True, allowed_private_hosts=frozenset({"127.0.0.1"}))
    assert str(await resolve_destination(httpx.URL("http://127.0.0.1:9/"), policy)) == "127.0.0.1"


async def test_unresolvable_host_is_not_sent() -> None:
    with pytest.raises(OutboundHttpError) as exc_info:
        await resolve_destination(httpx.URL("https://no-existe.invalid/"), STRICT)
    assert exc_info.value.kind == FailureKind.NOT_SENT


# ------------------------------------------------------------------ clasificación y backoff


@pytest.mark.parametrize(
    ("status", "success", "expected"),
    [
        (200, None, None),
        (204, None, None),
        (201, [201], None),
        (200, [201], FailureKind.PERMANENT),
        (404, None, FailureKind.PERMANENT),
        (400, None, FailureKind.PERMANENT),
        (408, None, FailureKind.REJECTED),
        (429, None, FailureKind.REJECTED),
        (503, None, FailureKind.REJECTED),
        (500, None, FailureKind.UNKNOWN),
        (502, None, FailureKind.UNKNOWN),
        (504, None, FailureKind.UNKNOWN),
        (302, None, FailureKind.PERMANENT),  # no se siguen redirecciones
    ],
)
def test_classify_status(
    status: int, success: list[int] | None, expected: FailureKind | None
) -> None:
    assert classify_status(status, success) == expected


def test_parse_retry_after() -> None:
    assert parse_retry_after("7") == 7.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("basura") is None
    future = (utcnow() + timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert 25 <= (parse_retry_after(future) or 0) <= 31


def test_backoff_grows_exponentially_with_bounded_jitter() -> None:
    policy = RetryPolicy(initial_backoff_seconds=1, backoff_multiplier=2, max_backoff_seconds=60)
    rng = random.Random(1)  # noqa: S311  # determinista a propósito en la prueba
    for attempt, base in [(1, 1), (2, 2), (3, 4), (4, 8), (10, 60)]:
        delay = compute_backoff(policy, attempt, rng=rng)
        assert base / 2 <= delay <= base, (attempt, delay)


def test_backoff_respects_retry_after() -> None:
    policy = RetryPolicy(initial_backoff_seconds=1)
    assert compute_backoff(policy, 1, retry_after=30) == 30


# ------------------------------------------------------------------ estados


def test_valid_and_invalid_transitions() -> None:
    assert_run_transition(RunStatus.PENDING, RunStatus.RUNNING)
    assert_step_transition(StepStatus.RUNNING, StepStatus.NEEDS_REVIEW)
    with pytest.raises(InvalidTransitionError):
        assert_run_transition(RunStatus.SUCCEEDED, RunStatus.RUNNING)
    with pytest.raises(InvalidTransitionError):
        assert_step_transition(StepStatus.SUCCEEDED, StepStatus.RUNNING)
    with pytest.raises(InvalidTransitionError):
        assert_step_transition(StepStatus.PENDING, StepStatus.SUCCEEDED)


# ------------------------------------------------------------------ decisión ante un fallo

DEFINITION = WorkflowDefinition.model_validate(
    {
        "start": "call",
        "steps": [
            {
                "id": "call",
                "type": "http_request",
                "config": {"method": "POST", "url": "https://x.example.com"},
                "retry": {"max_attempts": 3},
            }
        ],
    }
)


def _state(side_effect: str, attempts: int = 1) -> tuple[WorkflowRun, StepRun]:
    now = utcnow()
    run = WorkflowRun(
        id=uuid.uuid4(), status=RunStatus.RUNNING, deadline_at=now + timedelta(hours=1)
    )
    step = StepRun(
        id=uuid.uuid4(),
        run_id=run.id,
        step_id="call",
        status=StepStatus.RUNNING,
        side_effect=side_effect,
        attempt_count=attempts,
        max_attempts=3,
        timeout_seconds=10,
        sequence=1,
    )
    return run, step


@pytest.mark.parametrize(
    ("kind", "side_effect", "attempts", "expected_label", "run_status"),
    [
        # El caso clave: resultado desconocido + no idempotente → revisión, nunca reintento.
        (FailureKind.UNKNOWN, "non_idempotent", 1, "needs_review", RunStatus.NEEDS_REVIEW),
        (FailureKind.UNKNOWN, "idempotent", 1, "retry_scheduled", RunStatus.RUNNING),
        (FailureKind.UNKNOWN, "none", 1, "retry_scheduled", RunStatus.RUNNING),
        # No enviado o rechazado: seguro reintentar aunque no sea idempotente.
        (FailureKind.NOT_SENT, "non_idempotent", 1, "retry_scheduled", RunStatus.RUNNING),
        (FailureKind.REJECTED, "non_idempotent", 2, "retry_scheduled", RunStatus.RUNNING),
        # Intentos agotados o error permanente → fallo.
        (FailureKind.NOT_SENT, "none", 3, "failed", RunStatus.FAILED),
        (FailureKind.PERMANENT, "none", 1, "failed", RunStatus.FAILED),
    ],
)
def test_failure_decision_table(
    kind: FailureKind,
    side_effect: str,
    attempts: int,
    expected_label: str,
    run_status: RunStatus,
) -> None:
    run, step = _state(side_effect, attempts)
    now = utcnow()
    label, item = apply_failure(
        run, step, DEFINITION, StepError(kind, "x", "y"), now, make_settings()
    )
    assert label == expected_label
    assert run.status == run_status
    if expected_label == "retry_scheduled":
        assert step.next_attempt_at is not None
        assert step.next_attempt_at > now
        assert item is not None
    else:
        assert item is None


# ------------------------------------------------------------------ entrada de la ejecución

FIELDS = [
    InputField(name="id", type="string"),
    InputField(name="qty", type="integer"),
    InputField(name="note", type="string", required=False),
]


def test_valid_input() -> None:
    data: dict[str, Any] = {"id": "a", "qty": 2}
    assert validate_run_input(FIELDS, data) == data


@pytest.mark.parametrize(
    "data",
    [
        {"qty": 1},  # falta id
        {"id": "a", "qty": "2"},  # tipo incorrecto
        {"id": "a", "qty": True},  # bool no es integer
        {"id": "a", "qty": 1, "extra": 1},  # no declarado
    ],
)
def test_invalid_input(data: dict[str, Any]) -> None:
    with pytest.raises(InvalidRunInputError):
        validate_run_input(FIELDS, data)


def test_sensitive_response_headers_are_not_stored() -> None:
    from nexusflow.engine.executors import REDACTED, safe_response_headers

    stored = safe_response_headers(
        {
            "Content-Type": "application/json",
            "Set-Cookie": "session=abc123; HttpOnly",
            "X-Api-Key": "clave-del-socio",
            "X-Echo": "contiene token-secreto-xyz",
        },
        secrets=["token-secreto-xyz"],
    )
    assert stored["Content-Type"] == "application/json"
    assert stored["Set-Cookie"] == REDACTED
    assert stored["X-Api-Key"] == REDACTED
    assert stored["X-Echo"] == f"contiene {REDACTED}"
