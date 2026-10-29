"""Datos de demostración (sintéticos e idempotentes).

    nexusflow seed-demo [--runs 6]

Crea cuatro usuarios (uno por rol), flujos publicados de ejemplo y algunas ejecuciones para
que el panel tenga datos. Si se vuelve a ejecutar, no duplica nada.

- La contraseña de los usuarios demo se lee de NEXUSFLOW_DEMO_PASSWORD; si no existe, se
  genera una aleatoria y se muestra una sola vez. Nunca hay contraseñas en el código.
- Si están definidas MOCK_PARTNER_CRM_TOKEN y MOCK_PARTNER_PAYMENTS_KEY (perfil `demo` de
  compose), se añaden credenciales cifradas y un flujo de cobro contra la API simulada.
- Todos los datos son ficticios: dominios example.com y clientes inventados.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexusflow.audit.service import RequestContext
from nexusflow.auth.models import User
from nexusflow.auth.passwords import hash_password
from nexusflow.auth.permissions import Role
from nexusflow.auth.schemas import RegisterRequest
from nexusflow.core.config import Settings
from nexusflow.engine import service as runs
from nexusflow.engine.dispatch import Dispatcher
from nexusflow.engine.schemas import RunCreate
from nexusflow.integrations import service as credentials
from nexusflow.integrations.crypto import CredentialCipher
from nexusflow.integrations.models import Credential
from nexusflow.integrations.schemas import CredentialCreate
from nexusflow.schedules import service as schedules
from nexusflow.schedules.models import WorkflowSchedule
from nexusflow.schedules.schemas import ScheduleCreate
from nexusflow.workflows import service as workflows
from nexusflow.workflows.dsl import WorkflowDefinition
from nexusflow.workflows.models import Workflow
from nexusflow.workflows.schemas import WorkflowCreate

CTX = RequestContext(request_id="seed-demo")
DEMO_USERS: dict[str, Role] = {
    "demo-admin@example.com": Role.ADMIN,
    "demo-editor@example.com": Role.EDITOR,
    "demo-operator@example.com": Role.OPERATOR,
    "demo-viewer@example.com": Role.VIEWER,
}

CLASSIFY_ORDER: dict[str, Any] = {
    "inputs": [
        {"name": "order_id", "type": "string"},
        {"name": "amount", "type": "number"},
        {"name": "country", "type": "string"},
    ],
    "start": "is_large",
    "steps": [
        {
            "id": "is_large",
            "type": "condition",
            "config": {
                "when": {
                    "any": [
                        {"field": "input.amount", "op": "gte", "value": 1000},
                        {"field": "input.country", "op": "not_in", "value": ["CO", "MX", "ES"]},
                    ]
                }
            },
            "on_true": "manual_review",
            "on_false": "auto_approve",
        },
        {
            "id": "manual_review",
            "type": "transform",
            "config": {"output": {"order": "{{ input.order_id }}", "decision": "revisión"}},
        },
        {
            "id": "auto_approve",
            "type": "transform",
            "config": {"output": {"order": "{{ input.order_id }}", "decision": "aprobado"}},
        },
    ],
}

DELAYED_CONFIRMATION: dict[str, Any] = {
    "inputs": [{"name": "ticket", "type": "string"}],
    "start": "wait",
    "steps": [
        {"id": "wait", "type": "delay", "config": {"seconds": 2}, "next": "confirm"},
        {
            "id": "confirm",
            "type": "transform",
            "config": {"output": {"ticket": "{{ input.ticket }}", "status": "confirmado"}},
        },
    ],
}

VIP_CHARGE: dict[str, Any] = {
    "inputs": [{"name": "customer_id", "type": "string"}, {"name": "amount", "type": "number"}],
    "start": "lookup",
    "steps": [
        {
            "id": "lookup",
            "type": "http_request",
            "config": {
                "method": "GET",
                "url": "http://mock-partner:8080/crm/customers/{{ input.customer_id }}",
                "credential": "demo_crm",
            },
            "retry": {"max_attempts": 3, "initial_backoff_seconds": 1},
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
                # La pasarela simulada falla dos veces con 503: se ven los reintentos.
                "url": "http://mock-partner:8080/payments/charges?fail=2&key=demo-{{ run.id }}",
                "credential": "demo_pagos",
                "body": {"customer_id": "{{ input.customer_id }}", "amount": "{{ input.amount }}"},
            },
            "side_effect": "idempotent",
            "retry": {"max_attempts": 4, "initial_backoff_seconds": 1},
        },
    ],
}

SAMPLE_INPUTS: dict[str, list[dict[str, Any]]] = {
    "Demo · Clasificar pedido": [
        {"order_id": "PED-1001", "amount": 120, "country": "CO"},
        {"order_id": "PED-1002", "amount": 2500, "country": "MX"},
        {"order_id": "PED-1003", "amount": 80, "country": "AR"},
        {"order_id": "PED-1004", "amount": 640, "country": "ES"},
    ],
    "Demo · Confirmación diferida": [{"ticket": "TCK-77"}],
    # Clientes que existen en mock-partner (c-100 y c-300 son VIP).
    "Demo · Cobro a cliente VIP": [
        {"customer_id": "c-100", "amount": 49.9},
        {"customer_id": "c-300", "amount": 15.0},
        # Cliente inexistente: la consulta devuelve 404 y la ejecución falla, para que el
        # panel muestre un error real (clasificado, sin reintentos inútiles).
        {"customer_id": "c-999", "amount": 10.0},
    ],
}


@dataclass
class SeedReport:
    password_generated: str | None = None
    users_created: list[str] = field(default_factory=list)
    workflows_created: list[str] = field(default_factory=list)
    credentials_created: list[str] = field(default_factory=list)
    schedules_created: list[str] = field(default_factory=list)
    runs_started: int = 0


async def _ensure_users(session: AsyncSession, password: str, report: SeedReport) -> User:
    RegisterRequest.model_validate({"email": "demo-admin@example.com", "password": password})
    admin: User | None = None
    for email, role in DEMO_USERS.items():
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, password_hash=hash_password(password), role=role)
            session.add(user)
            report.users_created.append(email)
        if role is Role.ADMIN:
            admin = user
    await session.commit()
    if admin is None:  # imposible: DEMO_USERS siempre incluye un admin
        raise RuntimeError("DEMO_USERS no define un administrador")
    return admin


async def _ensure_credentials(
    session: AsyncSession, cipher: CredentialCipher, admin: User, report: SeedReport
) -> bool:
    crm_token = os.environ.get("MOCK_PARTNER_CRM_TOKEN")
    payments_key = os.environ.get("MOCK_PARTNER_PAYMENTS_KEY")
    if not crm_token or not payments_key:
        return False
    wanted = [
        CredentialCreate(
            name="demo_crm",
            type="bearer_token",
            description="CRM simulado (mock-partner)",
            allowed_hosts=["mock-partner"],
            secret={"token": crm_token},
        ),
        CredentialCreate(
            name="demo_pagos",
            type="api_key_header",
            description="Pasarela de pagos simulada (mock-partner)",
            allowed_hosts=["mock-partner"],
            secret={"header_name": "X-Api-Key", "value": payments_key},
        ),
    ]
    for data in wanted:
        if await session.scalar(select(Credential).where(Credential.name == data.name)) is None:
            await credentials.create_credential(session, cipher, admin, data, CTX)
            report.credentials_created.append(data.name)
    return True


async def _ensure_workflow(
    session: AsyncSession,
    admin: User,
    name: str,
    description: str,
    definition: dict[str, Any],
    report: SeedReport,
) -> Workflow:
    existing = await session.scalar(select(Workflow).where(Workflow.name == name))
    if existing is not None:
        return existing
    state = await workflows.create_workflow(
        session,
        admin,
        WorkflowCreate(
            name=name,
            description=description,
            definition=WorkflowDefinition.model_validate(definition),
        ),
        CTX,
    )
    await workflows.publish(session, admin, state.workflow.id, 1, CTX)
    report.workflows_created.append(name)
    return state.workflow


# (flujo, nombre, cron, zona, entrada). Una sin pausas largas (cada 15 min) para que, con
# `beat` en marcha, se vea un disparo real poco después de arrancar la demostración.
DEMO_SCHEDULES: list[tuple[str, str, str, str, dict[str, Any]]] = [
    (
        "Demo · Clasificar pedido",
        "Demo · Pedidos de cada día laborable",
        "0 9 * * 1-5",
        "Europe/Madrid",
        {"order_id": "PED-CRON", "amount": 99, "country": "ES"},
    ),
    (
        "Demo · Confirmación diferida",
        "Demo · Confirmación cada 15 minutos",
        "*/15 * * * *",
        "UTC",
        {"ticket": "TCK-CRON"},
    ),
]


async def _ensure_schedules(
    session: AsyncSession, admin: User, created: dict[str, Workflow], report: SeedReport
) -> None:
    for workflow_name, name, cron, zone, data in DEMO_SCHEDULES:
        workflow = created[workflow_name]
        exists = await session.scalar(
            select(WorkflowSchedule.id).where(
                WorkflowSchedule.workflow_id == workflow.id, WorkflowSchedule.name == name
            )
        )
        if exists is not None:
            continue
        await schedules.create_schedule(
            session,
            admin,
            workflow.id,
            ScheduleCreate(name=name, cron_expression=cron, timezone=zone, input=data),
            CTX,
        )
        report.schedules_created.append(name)


async def seed_demo(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    dispatcher: Dispatcher,
    *,
    runs_per_flow: int,
    password: str | None = None,
) -> SeedReport:
    report = SeedReport()
    if password is None:
        password = secrets.token_urlsafe(12) + "-Demo"
        report.password_generated = password

    cipher = CredentialCipher(settings.credentials_encryption_keys.get_secret_value())
    async with sessions() as session:
        admin = await _ensure_users(session, password, report)
        with_partner = await _ensure_credentials(session, cipher, admin, report)

        flows = [
            (
                "Demo · Clasificar pedido",
                "Aprueba pedidos pequeños y manda a revisión los grandes o de otros países.",
                CLASSIFY_ORDER,
            ),
            (
                "Demo · Confirmación diferida",
                "Espera 2 segundos (paso delay programado, no bloqueante) y confirma.",
                DELAYED_CONFIRMATION,
            ),
        ]
        if with_partner:
            flows.append(
                (
                    "Demo · Cobro a cliente VIP",
                    "Consulta el CRM con un token cifrado y cobra de forma idempotente "
                    "(la pasarela falla 2 veces).",
                    VIP_CHARGE,
                )
            )
        created = {
            name: await _ensure_workflow(session, admin, name, description, definition, report)
            for name, description, definition in flows
        }
        await _ensure_schedules(session, admin, created, report)

    for name, workflow in created.items():
        samples = SAMPLE_INPUTS.get(name, [])
        for i in range(runs_per_flow):
            if not samples:
                break
            async with sessions() as session:
                result = await runs.create_run(
                    session,
                    settings,
                    workflow_id=workflow.id,
                    data=RunCreate(input=samples[i % len(samples)]),
                    actor_id=admin.id,
                    trigger_type="manual",
                    context=CTX,
                )
            await dispatcher.dispatch(result.dispatch)
            report.runs_started += 1
    return report
