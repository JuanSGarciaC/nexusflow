"""Arnés para probar el motor sin broker: el dispatcher graba los mensajes y el arnés
los procesa en orden, como lo haría un worker (respetando las esperas)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nexusflow.audit.service import RequestContext
from nexusflow.auth.models import User
from nexusflow.auth.permissions import Role
from nexusflow.core.config import Settings
from nexusflow.engine import service as runs
from nexusflow.engine.celery_config import egress_policy
from nexusflow.engine.dispatch import DELIVER_WEBHOOK_TASK, RecordingDispatcher
from nexusflow.engine.executors import CredentialResolver, ExecutorDeps
from nexusflow.engine.models import StepAttempt, StepRun, WorkflowRun
from nexusflow.engine.runner import Engine
from nexusflow.engine.schemas import RunCreate
from nexusflow.integrations.crypto import CredentialCipher
from nexusflow.integrations.service import DbCredentialResolver
from nexusflow.webhooks.delivery import WebhookDeliverer
from nexusflow.workflows import service as workflows
from nexusflow.workflows.dsl import WorkflowDefinition
from nexusflow.workflows.schemas import WorkflowCreate

CTX = RequestContext(ip_address="127.0.0.1", request_id="engine-test")


@dataclass
class Harness:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    dispatcher: RecordingDispatcher
    engine: Engine
    actor_id: uuid.UUID
    deliverer: WebhookDeliverer

    async def publish(self, definition: dict[str, Any], name: str | None = None) -> uuid.UUID:
        async with self.sessions() as s:
            actor = await s.get(User, self.actor_id)
            assert actor is not None
            state = await workflows.create_workflow(
                s,
                actor,
                WorkflowCreate(
                    name=name or f"wf-{uuid.uuid4().hex[:8]}",
                    definition=WorkflowDefinition.model_validate(definition),
                ),
                CTX,
            )
            workflow_id = state.workflow.id
        async with self.sessions() as s:
            actor = await s.get(User, self.actor_id)
            assert actor is not None
            await workflows.publish(s, actor, workflow_id, 1, CTX)
        return workflow_id

    async def start(
        self, workflow_id: uuid.UUID, data: dict[str, Any] | None = None, key: str | None = None
    ) -> uuid.UUID:
        async with self.sessions() as s:
            created = await runs.create_run(
                s,
                self.settings,
                workflow_id=workflow_id,
                data=RunCreate(input=data or {}, idempotency_key=key),
                actor_id=self.actor_id,
                trigger_type="manual",
                context=CTX,
            )
        await self.dispatcher.dispatch(created.dispatch)
        return created.run.id

    async def drain(self, *, max_messages: int = 100) -> list[str]:
        """Procesa los mensajes encolados hasta vaciar la cola. Devuelve las etiquetas."""
        labels: list[str] = []
        for _ in range(max_messages):
            items = self.dispatcher.drain()
            if not items:
                return labels
            for item in items:
                if item.task == DELIVER_WEBHOOK_TASK:
                    # Las entregas se procesan sin esperar su backoff (minutos en producción).
                    labels.append("webhook:" + await self.deliverer.deliver(item.step_run_id))
                    continue
                if item.countdown:
                    await asyncio.sleep(item.countdown)
                labels.append(await self.engine.process_step(item.step_run_id))
        raise AssertionError("La ejecución no terminó: posible bucle de mensajes")

    async def run(self, run_id: uuid.UUID) -> WorkflowRun:
        async with self.sessions() as s:
            found = await s.get(WorkflowRun, run_id)
            assert found is not None
            return found

    async def steps(self, run_id: uuid.UUID) -> dict[str, StepRun]:
        async with self.sessions() as s:
            rows = await s.scalars(select(StepRun).where(StepRun.run_id == run_id))
            return {step.step_id: step for step in rows}

    async def attempts(self, step_run_id: uuid.UUID) -> list[StepAttempt]:
        async with self.sessions() as s:
            rows = await s.scalars(
                select(StepAttempt)
                .where(StepAttempt.step_run_id == step_run_id)
                .order_by(StepAttempt.attempt_number)
            )
            return list(rows)

    async def fetch(self, statement: str, **params: Any) -> list[dict[str, Any]]:
        async with self.sessions() as s:
            result = await s.execute(text(statement), params)
            return [dict(row._mapping) for row in result]

    async def sql(self, statement: str, **params: Any) -> None:
        async with self.sessions() as s:
            await s.execute(text(statement), params)
            await s.commit()


@asynccontextmanager
async def engine_harness(
    settings: Settings, resolve_credential: CredentialResolver | None = None
) -> AsyncIterator[Harness]:
    db = create_async_engine(settings.database_url.get_secret_value(), pool_size=5)
    sessions = async_sessionmaker(db, expire_on_commit=False)
    cipher = CredentialCipher(settings.credentials_encryption_keys.get_secret_value())
    if resolve_credential is None:
        # Igual que en el worker real: credenciales cifradas leídas de PostgreSQL.
        resolve_credential = DbCredentialResolver(sessions, cipher)
    dispatcher = RecordingDispatcher()
    async with httpx.AsyncClient(trust_env=False) as http, sessions() as s:
        user = User(
            email=f"engine-{uuid.uuid4().hex[:6]}@example.com",
            password_hash="x",
            role=Role.ADMIN,
        )
        s.add(user)
        await s.commit()
        engine = Engine(
            sessions,
            settings,
            dispatcher,
            ExecutorDeps(
                http=http,
                egress=egress_policy(settings),
                max_output_bytes=settings.step_output_max_bytes,
                resolve_credential=resolve_credential,
            ),
        )
        deliverer = WebhookDeliverer(
            sessions, settings, http, egress_policy(settings), cipher, dispatcher
        )
        try:
            yield Harness(settings, sessions, dispatcher, engine, user.id, deliverer)
        finally:
            await db.dispose()
