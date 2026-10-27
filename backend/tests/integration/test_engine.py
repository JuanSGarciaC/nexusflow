"""Motor de ejecución contra PostgreSQL real y una API externa real (local)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any

import pytest

from nexusflow.audit.service import RequestContext
from nexusflow.core.errors import ConflictError
from nexusflow.db.types import utcnow
from nexusflow.engine import service as runs
from nexusflow.engine.models import RunStatus, StepStatus
from nexusflow.engine.schemas import ResolveStep, RunCreate
from tests.integration.conftest import integration_settings
from tests.support.engine_harness import Harness, engine_harness
from tests.support.partner import STATE, serve_partner

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def partner_url() -> Iterator[str]:
    with serve_partner() as url:
        yield url


@pytest.fixture
async def h(partner_url: str) -> AsyncIterator[Harness]:
    STATE.reset()
    settings = integration_settings(
        egress_allow_http=True,
        egress_allowed_private_hosts=["127.0.0.1"],
        step_output_max_bytes=4096,
    )
    async with engine_harness(settings) as harness:
        yield harness


def http_step(
    step_id: str,
    url: str,
    *,
    method: str = "GET",
    next_step: str | None = None,
    attempts: int = 1,
    timeout: int = 5,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": step_id,
        "type": "http_request",
        "config": {"method": method, "url": url, **extra.pop("config", {})},
        "retry": {"max_attempts": attempts, "initial_backoff_seconds": 0.1},
        "timeout_seconds": timeout,
        "next": next_step,
        **extra,
    }


def single(step: dict[str, Any], inputs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"inputs": inputs or [], "start": step["id"], "steps": [step]}


# ------------------------------------------------------------------ camino feliz


async def test_happy_path_with_condition_and_transform(h: Harness, partner_url: str) -> None:
    wid = await h.publish(
        {
            "inputs": [{"name": "customer", "type": "string"}],
            "start": "lookup",
            "steps": [
                http_step(
                    "lookup", f"{partner_url}/customers/{{{{ input.customer }}}}", next_step="vip"
                ),
                {
                    "id": "vip",
                    "type": "condition",
                    "config": {
                        "when": {
                            "field": "steps.lookup.output.body.tier",
                            "op": "eq",
                            "value": "vip",
                        }
                    },
                    "on_true": "summary",
                },
                {
                    "id": "summary",
                    "type": "transform",
                    "config": {
                        "output": {
                            "customer": "{{ steps.lookup.output.body.id }}",
                            "message": "Cliente {{ input.customer }} es VIP",
                        }
                    },
                },
            ],
        }
    )
    run_id = await h.start(wid, {"customer": "vip-42"})
    assert await h.drain() == ["succeeded", "succeeded", "succeeded"]

    run = await h.run(run_id)
    assert run.status == RunStatus.SUCCEEDED
    assert run.output == {"customer": "vip-42", "message": "Cliente vip-42 es VIP"}
    assert run.started_at is not None
    assert run.finished_at is not None
    steps = await h.steps(run_id)
    assert [steps[s].sequence for s in ("lookup", "vip", "summary")] == [1, 2, 3]
    assert steps["vip"].output == {"result": True}
    lookup_output = steps["lookup"].output
    assert lookup_output is not None
    assert lookup_output["status_code"] == 200


async def test_condition_false_branch_ends_run(h: Harness, partner_url: str) -> None:
    wid = await h.publish(
        {
            "start": "lookup",
            "steps": [
                http_step("lookup", f"{partner_url}/customers/basic-1", next_step="vip"),
                {
                    "id": "vip",
                    "type": "condition",
                    "config": {
                        "when": {
                            "field": "steps.lookup.output.body.tier",
                            "op": "eq",
                            "value": "vip",
                        }
                    },
                    "on_true": "never",
                },
                {"id": "never", "type": "transform", "config": {"output": {"x": 1}}},
            ],
        }
    )
    run_id = await h.start(wid)
    await h.drain()
    assert (await h.run(run_id)).status == RunStatus.SUCCEEDED
    assert "never" not in await h.steps(run_id)


# ------------------------------------------------------------------ reintentos


async def test_transient_errors_are_retried_with_history(h: Harness, partner_url: str) -> None:
    wid = await h.publish(single(http_step("call", f"{partner_url}/flaky/a?fail=2", attempts=3)))
    run_id = await h.start(wid)
    labels = await h.drain()
    assert labels == ["retry_scheduled", "retry_scheduled", "succeeded"]

    step = (await h.steps(run_id))["call"]
    assert step.status == StepStatus.SUCCEEDED
    assert step.attempt_count == 3
    attempts = await h.attempts(step.id)
    assert [(a.outcome, a.http_status) for a in attempts] == [
        ("failed", 503),
        ("failed", 503),
        ("succeeded", None),
    ]
    assert all(a.duration_ms is not None for a in attempts)


async def test_retries_exhausted_fail_the_run(h: Harness, partner_url: str) -> None:
    wid = await h.publish(single(http_step("call", f"{partner_url}/flaky/b?fail=9", attempts=2)))
    run_id = await h.start(wid)
    assert await h.drain() == ["retry_scheduled", "failed"]
    run = await h.run(run_id)
    assert run.status == RunStatus.FAILED
    assert run.error is not None
    assert run.error["step_id"] == "call"
    assert run.error["http_status"] == 503


async def test_permanent_error_is_not_retried(h: Harness, partner_url: str) -> None:
    wid = await h.publish(single(http_step("call", f"{partner_url}/status/404", attempts=5)))
    run_id = await h.start(wid)
    assert await h.drain() == ["failed"]
    assert STATE.calls["status"] == 1
    assert (await h.run(run_id)).error["class"] == "http_404"  # type: ignore[index]


async def test_retry_after_header_is_respected(h: Harness, partner_url: str) -> None:
    wid = await h.publish(
        single(
            http_step("call", f"{partner_url}/flaky/c?fail=1&code=429&retry_after=1", attempts=2)
        )
    )
    run_id = await h.start(wid)
    await h.dispatcher.dispatch([])  # sin efecto: se procesa el primer mensaje a mano
    first = h.dispatcher.drain()
    assert await h.engine.process_step(first[0].step_run_id) == "retry_scheduled"
    retry = h.dispatcher.drain()
    assert retry[0].countdown is not None
    assert retry[0].countdown >= 1.0  # el servidor pidió 1 s
    step = (await h.steps(run_id))["call"]
    assert step.next_attempt_at is not None


# ------------------------------------------------------------------ idempotencia de efectos


async def test_unknown_outcome_on_non_idempotent_stops_for_review(
    h: Harness, partner_url: str
) -> None:
    # POST con timeout: el servidor probablemente cobró, pero no lo sabemos.
    wid = await h.publish(
        single(
            http_step(
                "charge", f"{partner_url}/charges?delay=2", method="POST", attempts=3, timeout=1
            )
        )
    )
    run_id = await h.start(wid)
    assert await h.drain() == ["needs_review"]
    await asyncio.sleep(1.5)  # deja que el servidor termine la petición lenta

    # Nunca se reintentó automáticamente (se cuentan solo las llamadas de esta ejecución).
    assert STATE.idempotency_keys.count(f"{run_id}:charge") == 1
    run = await h.run(run_id)
    assert run.status == RunStatus.NEEDS_REVIEW
    step = (await h.steps(run_id))["charge"]
    assert step.status == StepStatus.NEEDS_REVIEW
    assert step.error["class"] == "timeout"  # type: ignore[index]


async def test_unknown_outcome_on_idempotent_retries_with_same_key(
    h: Harness, partner_url: str
) -> None:
    wid = await h.publish(
        single(
            http_step("put", f"{partner_url}/slow?seconds=2", method="PUT", attempts=2, timeout=1)
        )
    )
    run_id = await h.start(wid)
    assert await h.drain() == ["retry_scheduled", "failed"]
    await asyncio.sleep(1.5)
    keys = STATE.idempotency_keys
    assert len(keys) == 2
    assert keys[0] == keys[1] == f"{run_id}:put"


async def test_duplicate_message_executes_once(h: Harness, partner_url: str) -> None:
    wid = await h.publish(single(http_step("charge", f"{partner_url}/charges", method="POST")))
    run_id = await h.start(wid)
    [item] = h.dispatcher.drain()
    # Dos workers reciben el mismo mensaje a la vez (entrega at-least-once).
    labels = await asyncio.gather(
        h.engine.process_step(item.step_run_id), h.engine.process_step(item.step_run_id)
    )
    assert sorted(labels) == ["skipped", "succeeded"]
    assert STATE.idempotency_keys.count(f"{run_id}:charge") == 1  # una sola llamada externa


# ------------------------------------------------------------------ seguridad y límites


async def test_ssrf_to_metadata_endpoint_blocked(h: Harness) -> None:
    wid = await h.publish(
        single(http_step("steal", "http://169.254.169.254/latest/meta-data/", attempts=3))
    )
    run_id = await h.start(wid)
    assert await h.drain() == ["failed"]
    assert (await h.run(run_id)).error["class"] == "egress_blocked"  # type: ignore[index]


async def test_response_size_limit(h: Harness, partner_url: str) -> None:
    wid = await h.publish(single(http_step("big", f"{partner_url}/big?bytes=50000")))
    run_id = await h.start(wid)
    assert await h.drain() == ["failed"]
    assert (await h.run(run_id)).error["class"] == "response_too_large"  # type: ignore[index]


async def test_idempotency_key_header_and_templates_sent(h: Harness, partner_url: str) -> None:
    wid = await h.publish(
        single(
            http_step(
                "echo",
                f"{partner_url}/echo",
                method="POST",
                config={
                    "headers": {"X-Order": "{{ input.order }}"},
                    "query": {"run": "{{ run.id }}"},
                    "body": {"qty": "{{ input.qty }}", "text": "n={{ input.qty }}"},
                },
            ),
            inputs=[{"name": "order", "type": "string"}, {"name": "qty", "type": "integer"}],
        )
    )
    run_id = await h.start(wid, {"order": "A-7", "qty": 3})
    await h.drain()
    echo_output = (await h.steps(run_id))["echo"].output
    assert echo_output is not None
    body = echo_output["body"]
    assert body["headers"]["x-order"] == "A-7"
    assert body["headers"]["idempotency-key"] == f"{run_id}:echo"
    assert body["query"] == {"run": str(run_id)}
    assert body["body"] == {"qty": 3, "text": "n=3"}  # tipo conservado en plantilla completa


# ------------------------------------------------------------------ recuperación (barrido)


async def test_sweep_redispatches_lost_messages_once(h: Harness, partner_url: str) -> None:
    wid = await h.publish(single(http_step("call", f"{partner_url}/customers/x")))
    run_id = await h.start(wid)
    h.dispatcher.drain()  # se "pierde" el mensaje (p. ej. Redis caído al encolar)
    await h.sql("UPDATE step_runs SET last_dispatched_at = now() - interval '5 minutes'")

    assert (await h.engine.sweep()).dispatched == 1
    assert (await h.engine.sweep()).dispatched == 0  # no reencola en bucle
    await h.drain()
    assert (await h.run(run_id)).status == RunStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("method", "expected_step", "expected_run"),
    [
        ("GET", StepStatus.RETRY_SCHEDULED, RunStatus.RUNNING),
        ("POST", StepStatus.NEEDS_REVIEW, RunStatus.NEEDS_REVIEW),
    ],
)
async def test_sweep_recovers_steps_of_dead_workers(
    h: Harness,
    partner_url: str,
    method: str,
    expected_step: StepStatus,
    expected_run: RunStatus,
) -> None:
    wid = await h.publish(
        single(http_step("call", f"{partner_url}/echo", method=method, attempts=3))
    )
    run_id = await h.start(wid)
    [item] = h.dispatcher.drain()
    # Un worker reclama el paso y muere sin registrar el resultado.
    assert await h.engine._claim(item.step_run_id) is not None
    await h.sql("UPDATE step_runs SET lease_expires_at = now() - interval '1 second'")

    assert (await h.engine.sweep()).recovered == 1
    step = (await h.steps(run_id))["call"]
    assert step.status == expected_step
    assert (await h.run(run_id)).status == expected_run
    attempts = await h.attempts(step.id)
    assert attempts[0].outcome == "abandoned"
    assert attempts[0].error_class == "worker_lost"


async def test_sweep_times_out_expired_runs(h: Harness, partner_url: str) -> None:
    wid = await h.publish(single(http_step("call", f"{partner_url}/customers/x")))
    run_id = await h.start(wid)
    h.dispatcher.drain()
    await h.sql("UPDATE workflow_runs SET deadline_at = now() - interval '1 second'")
    assert (await h.engine.sweep()).timed_out == 1
    assert (await h.run(run_id)).status == RunStatus.TIMED_OUT
    assert (await h.steps(run_id))["call"].status == StepStatus.CANCELLED


async def test_result_of_cancelled_run_is_discarded(h: Harness, partner_url: str) -> None:
    wid = await h.publish(
        {
            "start": "a",
            "steps": [
                http_step("a", f"{partner_url}/customers/x", next_step="b"),
                {"id": "b", "type": "transform", "config": {"output": {"x": 1}}},
            ],
        }
    )
    run_id = await h.start(wid)
    [item] = h.dispatcher.drain()
    claim = await h.engine._claim(item.step_run_id)
    assert claim is not None
    async with h.sessions() as s:
        await runs.cancel_run(s, h.actor_id, run_id, RequestContext())
    from nexusflow.engine.executors import StepSuccess

    assert await h.engine._record_success(claim, StepSuccess({"ok": 1}, "b")) == "stale"
    assert (await h.run(run_id)).status == RunStatus.CANCELLED
    assert "b" not in await h.steps(run_id)


# ------------------------------------------------------------------ esperas (delay)


async def test_delay_step_is_scheduled_not_slept(h: Harness) -> None:
    wid = await h.publish(
        {
            "start": "wait",
            "steps": [
                {"id": "wait", "type": "delay", "config": {"seconds": 1}, "next": "done"},
                {"id": "done", "type": "transform", "config": {"output": {"ok": True}}},
            ],
        }
    )
    run_id = await h.start(wid)
    [item] = h.dispatcher.items
    assert item.countdown is not None
    assert 0 < item.countdown <= 1
    # Un mensaje que llega antes de tiempo no ejecuta el paso.
    assert await h.engine.process_step(item.step_run_id) == "skipped"
    await h.drain()
    assert (await h.run(run_id)).status == RunStatus.SUCCEEDED


# ------------------------------------------------------------------ revisión manual


async def _needs_review(h: Harness, partner_url: str) -> Any:
    wid = await h.publish(
        {
            "start": "charge",
            "steps": [
                http_step(
                    "charge",
                    f"{partner_url}/charges?delay=2",
                    method="POST",
                    timeout=1,
                    next_step="receipt",
                ),
                {
                    "id": "receipt",
                    "type": "transform",
                    "config": {"output": {"charge": "{{ steps.charge.output.body.charge_id }}"}},
                },
            ],
        }
    )
    run_id = await h.start(wid)
    assert await h.drain() == ["needs_review"]
    return run_id


async def _resolve(h: Harness, run_id: Any, action: str, output: Any = None) -> None:
    async with h.sessions() as s:
        _, items = await runs.resolve_step(
            s,
            h.settings,
            actor_id=h.actor_id,
            run_id=run_id,
            step_id="charge",
            data=ResolveStep.model_validate({"action": action, "output": output}),
            context=RequestContext(),
        )
    await h.dispatcher.dispatch(items)


async def test_review_mark_succeeded_continues(h: Harness, partner_url: str) -> None:
    run_id = await _needs_review(h, partner_url)
    await _resolve(h, run_id, "mark_succeeded", {"body": {"charge_id": "ch_manual"}})
    await h.drain()
    run = await h.run(run_id)
    assert run.status == RunStatus.SUCCEEDED
    assert run.output == {"charge": "ch_manual"}


async def test_review_retry_reuses_idempotency_key(h: Harness, partner_url: str) -> None:
    run_id = await _needs_review(h, partner_url)
    await asyncio.sleep(1.5)  # la primera petición lenta termina en el servidor (sí cobró)
    STATE.healthy = True  # el servicio externo se recupera
    await _resolve(h, run_id, "retry")
    await h.drain()

    run = await h.run(run_id)
    assert run.status == RunStatus.SUCCEEDED
    # Ambas peticiones llevaron la misma clave: el servidor deduplicó y no cobró dos veces.
    key = f"{run_id}:charge"
    assert STATE.idempotency_keys.count(key) == 2
    assert run.output == {"charge": STATE.charges[key]}


async def test_review_mark_failed(h: Harness, partner_url: str) -> None:
    run_id = await _needs_review(h, partner_url)
    await _resolve(h, run_id, "mark_failed")
    assert (await h.run(run_id)).status == RunStatus.FAILED
    with pytest.raises(ConflictError):
        await _resolve(h, run_id, "retry")


# ------------------------------------------------------------------ creación idempotente


async def test_concurrent_run_creation_with_same_key(h: Harness, partner_url: str) -> None:
    wid = await h.publish(single(http_step("call", f"{partner_url}/customers/x")))

    async def create() -> Any:
        async with h.sessions() as s:
            created = await runs.create_run(
                s,
                h.settings,
                workflow_id=wid,
                data=RunCreate(input={}, idempotency_key="order-123"),
                actor_id=h.actor_id,
                trigger_type="manual",
                context=RequestContext(),
            )
            return created.run.id, created.created

    results = await asyncio.gather(*(create() for _ in range(5)))
    assert len({run_id for run_id, _ in results}) == 1
    assert sum(created for _, created in results) == 1


async def test_same_key_with_different_input_conflicts(h: Harness) -> None:
    wid = await h.publish(
        single(
            {"id": "t", "type": "transform", "config": {"output": {"v": "{{ input.v }}"}}},
            inputs=[{"name": "v", "type": "integer"}],
        )
    )
    await h.start(wid, {"v": 1}, key="k1")
    with pytest.raises(ConflictError):
        await h.start(wid, {"v": 2}, key="k1")


async def test_run_deadline_is_set(h: Harness) -> None:
    wid = await h.publish(single({"id": "t", "type": "transform", "config": {"output": {"a": 1}}}))
    run_id = await h.start(wid)
    run = await h.run(run_id)
    expected = run.created_at + timedelta(hours=h.settings.run_max_duration_hours)
    assert abs((run.deadline_at - expected).total_seconds()) < 1
    assert run.deadline_at > utcnow()
