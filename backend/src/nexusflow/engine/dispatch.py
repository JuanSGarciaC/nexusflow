"""Encolado de pasos para los workers.

El mensaje solo lleva el id del paso. Si el encolado falla (Redis caído) no se pierde
nada: el paso sigue en PostgreSQL como pendiente y el barrido periódico lo vuelve a encolar.
Por eso aquí los errores se registran pero no se propagan.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from nexusflow.core.logging import get_logger

if TYPE_CHECKING:
    from celery import Celery

logger = get_logger(__name__)

PROCESS_STEP_TASK = "nexusflow.process_step"
SWEEP_TASK = "nexusflow.sweep"


@dataclass(frozen=True, slots=True)
class DispatchItem:
    step_run_id: uuid.UUID
    countdown: float | None = None


class Dispatcher(Protocol):
    async def dispatch(self, items: list[DispatchItem]) -> None: ...


class CeleryDispatcher:
    def __init__(self, app: Celery) -> None:
        self._app = app

    def _send(self, items: list[DispatchItem]) -> None:
        for item in items:
            self._app.send_task(
                PROCESS_STEP_TASK,
                args=[str(item.step_run_id)],
                countdown=item.countdown or None,
            )

    async def dispatch(self, items: list[DispatchItem]) -> None:
        if not items:
            return
        try:
            # send_task hace E/S bloqueante: se ejecuta en un hilo.
            await asyncio.to_thread(self._send, items)
        except Exception as exc:  # el barrido reintentará el encolado
            logger.error("dispatch_failed", count=len(items), error_type=type(exc).__name__)


class RecordingDispatcher:
    """Dispatcher en memoria para pruebas: acumula lo que se habría encolado."""

    def __init__(self) -> None:
        self.items: list[DispatchItem] = []

    async def dispatch(self, items: list[DispatchItem]) -> None:
        self.items.extend(items)

    def drain(self) -> list[DispatchItem]:
        items, self.items = self.items, []
        return items
