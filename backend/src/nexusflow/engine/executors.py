"""Ejecución de un paso concreto. Sin acceso a la base de datos: entra contexto, sale
un resultado o un `StepError` clasificado. El runner decide qué hacer con él."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from nexusflow.engine.http_client import (
    EgressPolicy,
    FailureKind,
    OutboundHttpError,
    classify_status,
    parse_retry_after,
    send_request,
)
from nexusflow.workflows.dsl import (
    ConditionStep,
    DelayStep,
    HttpRequestStep,
    SideEffect,
    Step,
    TransformStep,
)
from nexusflow.workflows.expressions import ExpressionError, evaluate, render

# Recibe el nombre de una credencial y devuelve las cabeceras que hay que añadir.
CredentialResolver = Callable[[str], Awaitable[dict[str, str]]]


@dataclass
class StepError(Exception):
    kind: FailureKind
    error_class: str
    message: str
    http_status: int | None = None
    retry_after_seconds: float | None = None

    def __str__(self) -> str:
        return f"{self.error_class}: {self.message}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "class": self.error_class,
            "message": self.message,
            "http_status": self.http_status,
        }


@dataclass(frozen=True, slots=True)
class StepSuccess:
    output: Any
    next_step: str | None


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    run_id: str
    workflow_id: str
    input: dict[str, Any]
    outputs: dict[str, Any]
    idempotency_key: str

    def template_context(self) -> dict[str, Any]:
        return {
            "input": self.input,
            "steps": {step: {"output": out} for step, out in self.outputs.items()},
            "run": {"id": self.run_id, "workflow_id": self.workflow_id},
        }


@dataclass(frozen=True, slots=True)
class ExecutorDeps:
    http: httpx.AsyncClient
    egress: EgressPolicy
    max_output_bytes: int
    resolve_credential: CredentialResolver | None = None


def _render(value: Any, context: dict[str, Any]) -> Any:
    try:
        return render(value, context)
    except ExpressionError as exc:
        raise StepError(FailureKind.PERMANENT, "template_error", str(exc)) from exc


def _ensure_size(output: Any, limit: int) -> Any:
    size = len(json.dumps(output, default=str).encode())
    if size > limit:
        raise StepError(
            FailureKind.PERMANENT,
            "output_too_large",
            f"La salida del paso ocupa {size} bytes (máximo {limit})",
        )
    return output


def _snippet(body: Any) -> str:
    text = body if isinstance(body, str) else json.dumps(body, default=str, ensure_ascii=False)
    return text[:300]


async def _http(step: HttpRequestStep, ctx: ExecutionContext, deps: ExecutorDeps) -> StepSuccess:
    template = ctx.template_context()
    config = step.config
    url = _render(config.url, template)
    headers = {str(k): str(v) for k, v in _render(config.headers, template).items()}
    query = {str(k): str(v) for k, v in _render(config.query, template).items()}
    body = _render(config.body, template)
    if not isinstance(url, str):
        raise StepError(FailureKind.PERMANENT, "template_error", "La URL debe ser texto")

    if config.credential:
        if deps.resolve_credential is None:
            raise StepError(
                FailureKind.PERMANENT, "credential_unavailable", "No hay almacén de credenciales"
            )
        # Las cabeceras de la credencial se añaden al final: el flujo no puede sobrescribirlas.
        headers.update(await deps.resolve_credential(config.credential))

    if step.effective_side_effect != SideEffect.NONE:
        # Misma clave en todos los reintentos: el destino puede detectar duplicados.
        headers["Idempotency-Key"] = ctx.idempotency_key

    try:
        result = await send_request(
            deps.http,
            method=config.method,
            url=url,
            headers=headers,
            params=query,
            json_body=body,
            timeout_seconds=step.timeout_seconds,
            policy=deps.egress,
        )
    except OutboundHttpError as exc:
        raise StepError(
            exc.kind, exc.error_class, exc.message, exc.http_status, exc.retry_after_seconds
        ) from exc

    failure = classify_status(result.status_code, config.success_status)
    if failure is not None:
        raise StepError(
            failure,
            f"http_{result.status_code}",
            f"Respuesta {result.status_code}: {_snippet(result.body)}",
            http_status=result.status_code,
            retry_after_seconds=parse_retry_after(result.headers.get("retry-after")),
        )
    output = {"status_code": result.status_code, "headers": result.headers, "body": result.body}
    return StepSuccess(_ensure_size(output, deps.max_output_bytes), step.next)


async def execute_step(step: Step, ctx: ExecutionContext, deps: ExecutorDeps) -> StepSuccess:
    if isinstance(step, HttpRequestStep):
        return await _http(step, ctx, deps)
    if isinstance(step, TransformStep):
        output = _render(step.config.output, ctx.template_context())
        return StepSuccess(_ensure_size(output, deps.max_output_bytes), step.next)
    if isinstance(step, ConditionStep):
        result = evaluate(step.config.when, ctx.template_context())
        return StepSuccess({"result": result}, step.on_true if result else step.on_false)
    if isinstance(step, DelayStep):
        # La espera ya ocurrió: el paso se programó con next_attempt_at = ahora + segundos.
        return StepSuccess({"waited_seconds": step.config.seconds}, step.next)
    raise StepError(FailureKind.PERMANENT, "unsupported_step", "Tipo de paso no soportado")
