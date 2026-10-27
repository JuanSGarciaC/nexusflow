"""Health checks.

- /health/live: el proceso responde. No toca dependencias, para que un fallo de la base de
  datos no provoque reinicios en cascada del contenedor.
- /health/ready: el proceso puede atender tráfico (PostgreSQL y Redis accesibles).
  Devuelve 503 si alguna dependencia falla. El motivo detallado va a los logs, no a la
  respuesta, para no exponer topología interna.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from nexusflow import __version__
from nexusflow.core.logging import get_logger

router = APIRouter(prefix="/health", tags=["health"])
logger = get_logger(__name__)

CheckStatus = Literal["ok", "error"]


class LiveResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str = __version__


class CheckResult(BaseModel):
    status: CheckStatus
    latency_ms: float


class ReadyResponse(BaseModel):
    status: CheckStatus
    checks: dict[str, CheckResult]


async def check_database(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def check_redis(client: Redis) -> None:
    if not await client.ping():
        raise RuntimeError("PING sin respuesta")


async def _run_check(
    name: str, check: Callable[[], Awaitable[None]], limit_seconds: float
) -> CheckResult:
    start = time.perf_counter()
    status: CheckStatus = "ok"
    try:
        await asyncio.wait_for(check(), timeout=limit_seconds)
    except Exception as exc:  # cualquier fallo de la dependencia la marca como no lista
        status = "error"
        logger.warning("readiness_check_failed", check=name, error_type=type(exc).__name__)
    latency = round((time.perf_counter() - start) * 1000, 2)
    return CheckResult(status=status, latency_ms=latency)


@router.get("/live", response_model=LiveResponse, summary="Liveness")
async def live() -> LiveResponse:
    return LiveResponse()


@router.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Readiness",
    responses={503: {"model": ReadyResponse, "description": "Alguna dependencia no responde"}},
)
async def ready(request: Request, response: Response) -> ReadyResponse:
    state = request.app.state
    timeout: float = state.settings.readiness_timeout_seconds
    db_result, redis_result = await asyncio.gather(
        _run_check("database", lambda: check_database(state.engine), timeout),
        _run_check("redis", lambda: check_redis(state.redis), timeout),
    )
    checks = {"database": db_result, "redis": redis_result}
    overall: CheckStatus = "ok" if all(c.status == "ok" for c in checks.values()) else "error"
    if overall != "ok":
        response.status_code = 503
    return ReadyResponse(status=overall, checks=checks)
