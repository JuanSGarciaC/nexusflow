"""Aplicación Celery: worker y beat.

    celery -A nexusflow.engine.worker:celery_app worker --loglevel=INFO
    celery -A nexusflow.engine.worker:celery_app beat --loglevel=INFO

Configuración clave (ver celery_config.py):
- acks_late + reject_on_worker_lost: si el worker muere a mitad de tarea, el mensaje vuelve
  a la cola (entrega at-least-once). La idempotencia la garantiza el reclamo en PostgreSQL.
- prefetch_multiplier=1: un worker no acapara tareas que otro podría ejecutar.
- Sin backend de resultados: el estado está en PostgreSQL.

Cada proceso del worker mantiene su propio event loop, su pool de conexiones a la base de
datos y su cliente HTTP, creados al recibir la primera tarea (después del fork).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
from celery import Celery
from celery.signals import worker_process_shutdown

from nexusflow.core.config import Settings
from nexusflow.db.session import create_engine, create_session_factory
from nexusflow.engine.celery_config import create_celery, egress_policy
from nexusflow.engine.dispatch import PROCESS_STEP_TASK, SWEEP_TASK, CeleryDispatcher, Dispatcher
from nexusflow.engine.executors import CredentialResolver, ExecutorDeps
from nexusflow.engine.runner import Engine
from nexusflow.integrations.crypto import CredentialCipher
from nexusflow.integrations.service import DbCredentialResolver

T = TypeVar("T")


class WorkerRuntime:
    """Recursos de un proceso worker. Se crean perezosamente en el loop que los usará."""

    def __init__(
        self,
        settings: Settings,
        dispatcher: Dispatcher,
        resolve_credential: CredentialResolver | None = None,
    ) -> None:
        self.settings = settings
        self.loop = asyncio.new_event_loop()
        self._dispatcher = dispatcher
        self._resolve_credential = resolve_credential
        self._engine: Engine | None = None
        self._closers: list[Callable[[], Awaitable[None]]] = []

    async def _build(self) -> Engine:
        db = create_engine(self.settings)
        http = httpx.AsyncClient(verify=True, follow_redirects=False, trust_env=False)
        self._closers = [http.aclose, db.dispose]
        sessions = create_session_factory(db)
        resolver = self._resolve_credential or DbCredentialResolver(
            sessions,
            CredentialCipher(self.settings.credentials_encryption_keys.get_secret_value()),
        )
        return Engine(
            sessions,
            self.settings,
            self._dispatcher,
            ExecutorDeps(
                http=http,
                egress=egress_policy(self.settings),
                max_output_bytes=self.settings.step_output_max_bytes,
                resolve_credential=resolver,
            ),
        )

    def run(self, operation: Callable[[Engine], Awaitable[T]]) -> T:
        if self._engine is None:
            self._engine = self.loop.run_until_complete(self._build())
        return self.loop.run_until_complete(operation(self._engine))

    def close(self) -> None:
        for closer in self._closers:
            self.loop.run_until_complete(closer())
        self.loop.close()


def register_tasks(app: Celery, runtime: Callable[[], WorkerRuntime]) -> None:
    @app.task(
        name=PROCESS_STEP_TASK,
        # Red de seguridad: el paso más largo dura 300 s. Si se supera, el proceso se mata
        # y el barrido recupera el paso cuando venza su lease.
        soft_time_limit=360,
        time_limit=420,
    )
    def process_step(step_run_id: str) -> str:
        return runtime().run(lambda engine: engine.process_step(uuid.UUID(step_run_id)))

    @app.task(name=SWEEP_TASK, soft_time_limit=120, time_limit=180)
    def sweep() -> dict[str, int]:
        report = runtime().run(lambda engine: engine.sweep())
        return {
            "dispatched": report.dispatched,
            "recovered": report.recovered,
            "timed_out": report.timed_out,
        }


def build_worker_app(
    settings: Settings, resolve_credential: CredentialResolver | None = None
) -> tuple[Celery, Callable[[], WorkerRuntime]]:
    app = create_celery(settings)
    state: dict[str, WorkerRuntime] = {}

    def runtime() -> WorkerRuntime:
        # Un runtime por proceso (o por hilo de worker en pruebas), creado tras el fork.
        if "runtime" not in state:
            state["runtime"] = WorkerRuntime(settings, CeleryDispatcher(app), resolve_credential)
        return state["runtime"]

    def close() -> None:
        if "runtime" in state:
            state.pop("runtime").close()

    register_tasks(app, runtime)
    app.nexusflow_close = close
    return app, runtime


def _create_default_app() -> Celery:
    from nexusflow.core.config import get_settings
    from nexusflow.core.logging import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)
    app, _ = build_worker_app(settings)

    @worker_process_shutdown.connect
    def _shutdown(**_: Any) -> None:
        app.nexusflow_close()

    return app


def __getattr__(name: str) -> Any:
    # `celery -A nexusflow.engine.worker:celery_app` crea la app al pedirla, no al importar
    # el módulo: así las pruebas pueden importarlo sin variables de entorno.
    if name == "celery_app":
        app = _create_default_app()
        globals()["celery_app"] = app
        return app
    raise AttributeError(name)
