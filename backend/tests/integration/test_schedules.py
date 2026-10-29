"""Programaciones cron: API, permisos y disparador contra PostgreSQL y Redis reales."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from nexusflow.auth.permissions import Role
from nexusflow.db.types import utcnow
from nexusflow.engine.dispatch import RecordingDispatcher
from nexusflow.engine.models import WorkflowRun
from nexusflow.main import create_app
from nexusflow.schedules.models import WorkflowSchedule
from nexusflow.schedules.service import ScheduleFirer, fire_key
from tests.integration.conftest import (
    create_user,
    db_url,
    fetch_all,
    integration_settings,
    login_headers,
    run_db,
)

pytestmark = pytest.mark.integration

DEFINITION: dict[str, Any] = {
    "inputs": [{"name": "region", "type": "string"}],
    "start": "echo",
    "steps": [
        {"id": "echo", "type": "transform", "config": {"output": {"r": "{{ input.region }}"}}}
    ],
}


@pytest.fixture
def api() -> Iterator[TestClient]:
    app = create_app(integration_settings(), dispatcher=RecordingDispatcher())
    with TestClient(app, base_url="https://testserver") as c:
        yield c


@pytest.fixture
def editor(api: TestClient) -> dict[str, str]:
    create_user("cron-editor@example.com", Role.EDITOR)
    return login_headers(api, "cron-editor@example.com")


def _workflow(api: TestClient, headers: dict[str, str], *, publish: bool = True) -> str:
    wid: str = api.post(
        "/api/v1/workflows",
        json={"name": f"cron-{uuid.uuid4().hex[:6]}", "definition": DEFINITION},
        headers=headers,
    ).json()["id"]
    if publish:
        api.post(f"/api/v1/workflows/{wid}/publish", json={"expected_revision": 1}, headers=headers)
    return wid


def _schedule(
    api: TestClient, headers: dict[str, str], wid: str, **overrides: Any
) -> dict[str, Any]:
    body = {
        "name": "diario",
        "cron_expression": "0 9 * * *",
        "timezone": "Europe/Madrid",
        "input": {"region": "eu"},
        **overrides,
    }
    response = api.post(f"/api/v1/workflows/{wid}/schedules", json=body, headers=headers)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


# ------------------------------------------------------------------ API


def test_create_returns_the_next_fire_time(api: TestClient, editor: dict[str, str]) -> None:
    schedule = _schedule(api, editor, _workflow(api, editor))
    assert schedule["is_active"] is True
    assert schedule["next_run_at"] is not None
    assert schedule["last_run_at"] is None
    listed = api.get(f"/api/v1/workflows/{schedule['workflow_id']}/schedules", headers=editor)
    assert [s["id"] for s in listed.json()] == [schedule["id"]]


@pytest.mark.parametrize("role", [Role.VIEWER, Role.OPERATOR])
def test_only_editors_and_admins_manage_schedules(api: TestClient, role: Role) -> None:
    create_user("owner@example.com", Role.EDITOR)
    wid = _workflow(api, login_headers(api, "owner@example.com"))
    create_user(f"{role}@example.com", role)
    headers = login_headers(api, f"{role}@example.com")
    body = {"name": "x", "cron_expression": "* * * * *"}
    assert (
        api.post(f"/api/v1/workflows/{wid}/schedules", json=body, headers=headers).status_code
        == 403
    )
    assert api.get(f"/api/v1/workflows/{wid}/schedules", headers=headers).status_code == 403


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"cron_expression": "esto no es cron"}, "cron"),
        ({"cron_expression": "99 * * * *"}, "cron"),
        ({"timezone": "Mars/Base"}, "Zona horaria"),
    ],
)
def test_invalid_schedule_is_a_422(
    api: TestClient, editor: dict[str, str], overrides: dict[str, str], fragment: str
) -> None:
    wid = _workflow(api, editor)
    body = {"name": "x", "cron_expression": "* * * * *", **overrides}
    response = api.post(f"/api/v1/workflows/{wid}/schedules", json=body, headers=editor)
    assert response.status_code == 422
    assert fragment in response.text


def test_unknown_workflow_is_a_404(api: TestClient, editor: dict[str, str]) -> None:
    body = {"name": "x", "cron_expression": "* * * * *"}
    response = api.post(f"/api/v1/workflows/{uuid.uuid4()}/schedules", json=body, headers=editor)
    assert response.status_code == 404


def test_pause_resume_update_and_delete(api: TestClient, editor: dict[str, str]) -> None:
    schedule = _schedule(api, editor, _workflow(api, editor))
    url = f"/api/v1/schedules/{schedule['id']}"

    paused = api.patch(url, json={"is_active": False}, headers=editor).json()
    assert paused["is_active"] is False
    assert paused["next_run_at"] is None

    resumed = api.patch(url, json={"is_active": True}, headers=editor).json()
    assert resumed["next_run_at"] is not None

    changed = api.patch(url, json={"cron_expression": "*/5 * * * *"}, headers=editor).json()
    assert changed["cron_expression"] == "*/5 * * * *"
    assert changed["next_run_at"] != resumed["next_run_at"]

    bad = api.patch(url, json={"cron_expression": "nope nope"}, headers=editor)
    assert bad.status_code == 422
    assert (
        api.get(f"/api/v1/workflows/{schedule['workflow_id']}/schedules", headers=editor).json()[0][
            "cron_expression"
        ]
        == "*/5 * * * *"
    )  # el intento fallido no cambió nada

    assert api.delete(url, headers=editor).status_code == 204
    assert api.delete(url, headers=editor).status_code == 404


def test_schedule_changes_are_audited(api: TestClient, editor: dict[str, str]) -> None:
    schedule = _schedule(api, editor, _workflow(api, editor))
    api.patch(f"/api/v1/schedules/{schedule['id']}", json={"name": "otro"}, headers=editor)
    api.delete(f"/api/v1/schedules/{schedule['id']}", headers=editor)
    actions = [
        r["action"]
        for r in fetch_all(
            "SELECT action FROM audit_log WHERE action LIKE 'schedule.%' ORDER BY id"
        )
    ]
    assert actions == ["schedule.created", "schedule.updated", "schedule.deleted"]


# ------------------------------------------------------------------ disparador


def _firer(dispatcher: RecordingDispatcher, **overrides: object) -> tuple[ScheduleFirer, Any]:
    engine = create_async_engine(db_url(), poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return ScheduleFirer(sessions, integration_settings(**overrides), dispatcher), engine


def _sweep(dispatcher: RecordingDispatcher, *, times: int = 1, **overrides: object) -> Any:
    async def _go() -> list[Any]:
        firer, engine = _firer(dispatcher, **overrides)
        try:
            return [await firer.sweep() for _ in range(times)]
        finally:
            await engine.dispose()

    return asyncio.run(_go())


def _make_due(schedule_id: str, *, seconds_ago: int = 30) -> Any:
    due = utcnow() - timedelta(seconds=seconds_ago)

    async def _op(session: AsyncSession) -> None:
        await session.execute(
            update(WorkflowSchedule)
            .where(WorkflowSchedule.id == uuid.UUID(schedule_id))
            .values(next_run_at=due)
        )
        await session.commit()

    run_db(_op)
    return due


def _runs() -> list[WorkflowRun]:
    async def _op(session: AsyncSession) -> list[WorkflowRun]:
        return list(await session.scalars(select(WorkflowRun).order_by(WorkflowRun.created_at)))

    return run_db(_op)


def _row(schedule_id: str) -> WorkflowSchedule:
    async def _op(session: AsyncSession) -> WorkflowSchedule:
        return await session.get_one(WorkflowSchedule, uuid.UUID(schedule_id))

    return run_db(_op)


def test_due_schedule_creates_one_run_and_advances(api: TestClient, editor: dict[str, str]) -> None:
    schedule = _schedule(api, editor, _workflow(api, editor), cron_expression="*/10 * * * *")
    _make_due(schedule["id"])
    dispatcher = RecordingDispatcher()

    reports = _sweep(dispatcher, times=2)  # el segundo barrido no debe repetir nada

    assert (reports[0].fired, reports[1].fired) == (1, 0)
    runs = _runs()
    assert len(runs) == 1
    assert runs[0].trigger_type == "schedule"
    assert runs[0].input == {"region": "eu"}
    assert len(dispatcher.items) == 1  # el primer paso quedó encolado
    row = _row(schedule["id"])
    assert row.last_run_id == runs[0].id
    assert row.last_error is None
    assert row.next_run_at is not None
    assert row.next_run_at > utcnow()


def test_crash_between_creating_the_run_and_advancing_does_not_duplicate(
    api: TestClient, editor: dict[str, str]
) -> None:
    schedule = _schedule(api, editor, _workflow(api, editor), cron_expression="*/10 * * * *")
    due = _make_due(schedule["id"])
    _sweep(RecordingDispatcher())
    assert len(_runs()) == 1

    # Simula la caída: la ejecución existe pero `next_run_at` no llegó a avanzar.
    async def _rewind(session: AsyncSession) -> None:
        await session.execute(
            update(WorkflowSchedule)
            .where(WorkflowSchedule.id == uuid.UUID(schedule["id"]))
            .values(next_run_at=due)
        )
        await session.commit()

    run_db(_rewind)
    report = _sweep(RecordingDispatcher())[0]

    assert len(_runs()) == 1, "el reintento del disparo creó una segunda ejecución"
    assert report.fired == 1
    assert _row(schedule["id"]).next_run_at > utcnow()  # type: ignore[operator]
    assert _runs()[0].idempotency_key == fire_key(uuid.UUID(schedule["id"]), due)


def test_concurrent_sweeps_fire_once(api: TestClient, editor: dict[str, str]) -> None:
    schedule = _schedule(api, editor, _workflow(api, editor), cron_expression="*/10 * * * *")
    _make_due(schedule["id"])

    async def _go() -> None:
        dispatcher = RecordingDispatcher()
        pairs = [_firer(dispatcher) for _ in range(5)]
        try:
            await asyncio.gather(*(firer.sweep() for firer, _ in pairs))
        finally:
            for _, engine in pairs:
                await engine.dispose()

    asyncio.run(_go())
    assert len(_runs()) == 1


def test_paused_schedules_never_fire(api: TestClient, editor: dict[str, str]) -> None:
    schedule = _schedule(api, editor, _workflow(api, editor), is_active=False)
    assert schedule["next_run_at"] is None
    assert _sweep(RecordingDispatcher())[0].fired == 0
    assert _runs() == []


def test_missed_fire_beyond_the_grace_period_is_skipped_not_replayed(
    api: TestClient, editor: dict[str, str]
) -> None:
    schedule = _schedule(api, editor, _workflow(api, editor), cron_expression="*/10 * * * *")
    _make_due(schedule["id"], seconds_ago=3 * 3600)  # el sistema estuvo caído 3 horas

    report = _sweep(RecordingDispatcher())[0]

    assert (report.fired, report.skipped) == (0, 1)
    assert _runs() == []  # no se lanzan las 18 ejecuciones atrasadas
    row = _row(schedule["id"])
    assert row.last_error is not None
    assert "omitido" in row.last_error
    assert row.next_run_at is not None
    assert row.next_run_at > utcnow()


def test_failure_is_recorded_and_the_schedule_keeps_running(
    api: TestClient, editor: dict[str, str]
) -> None:
    wid = _workflow(api, editor, publish=False)  # sin versión publicada: no se puede ejecutar
    schedule = _schedule(api, editor, wid, cron_expression="*/10 * * * *")
    _make_due(schedule["id"])

    report = _sweep(RecordingDispatcher())[0]

    assert (report.fired, report.failed) == (0, 1)
    row = _row(schedule["id"])
    assert row.last_error is not None
    assert "publicada" in row.last_error
    assert row.is_active
    assert row.next_run_at is not None
    assert row.next_run_at > utcnow()


def test_invalid_input_is_recorded_as_an_error(api: TestClient, editor: dict[str, str]) -> None:
    schedule = _schedule(
        api, editor, _workflow(api, editor), cron_expression="*/10 * * * *", input={}
    )  # falta la entrada obligatoria `region`
    _make_due(schedule["id"])
    report = _sweep(RecordingDispatcher())[0]
    assert report.failed == 1
    assert _runs() == []
    assert _row(schedule["id"]).last_error


def test_archived_workflow_is_reported_not_executed(
    api: TestClient, editor: dict[str, str]
) -> None:
    wid = _workflow(api, editor)
    schedule = _schedule(api, editor, wid, cron_expression="*/10 * * * *")
    archived = api.patch(f"/api/v1/workflows/{wid}", json={"status": "archived"}, headers=editor)
    assert archived.status_code == 200, archived.text
    _make_due(schedule["id"])

    report = _sweep(RecordingDispatcher())[0]

    assert (report.fired, report.failed) == (0, 1)
    assert _runs() == []
    row = _row(schedule["id"])
    assert row.last_error is not None
    assert "archivado" in row.last_error
