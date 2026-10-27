"""Configuración de Celery sin efectos al importar.

La API la usa solo como productor (encola tareas); el módulo `worker` la usa para consumir.
"""

from __future__ import annotations

from celery import Celery

from nexusflow.core.config import Settings
from nexusflow.engine.dispatch import SWEEP_TASK
from nexusflow.engine.http_client import EgressPolicy


def egress_policy(settings: Settings) -> EgressPolicy:
    return EgressPolicy(
        allow_http=settings.egress_allow_http,
        allowed_private_hosts=frozenset(h.lower() for h in settings.egress_allowed_private_hosts),
        max_response_bytes=settings.step_output_max_bytes,
    )


def create_celery(settings: Settings) -> Celery:
    app = Celery("nexusflow", broker=settings.broker_url)
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_ignore_result=True,
        task_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        task_default_queue="nexusflow",
        broker_connection_retry_on_startup=True,
        # Mayor que la espera más larga que se encola con countdown.
        broker_transport_options={
            "visibility_timeout": settings.max_dispatch_countdown_seconds + 3600
        },
        worker_hijack_root_logger=False,
        beat_schedule={
            "nexusflow-sweep": {"task": SWEEP_TASK, "schedule": settings.sweep_interval_seconds}
        },
    )
    return app
