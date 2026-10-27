"""Errores de la API en formato RFC 9457 (Problem Details for HTTP APIs).

Principio: el cliente recibe un mensaje estable y sin detalles internos; el detalle
completo (traza, causa) queda solo en los logs, enlazado por request_id.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from nexusflow.core.logging import get_logger

PROBLEM_JSON = "application/problem+json"
PROBLEM_TYPE_BASE = "https://nexusflow.dev/problems/"

logger = get_logger(__name__)


class AppError(Exception):
    """Error de dominio con significado HTTP. Las subclases fijan status y code."""

    status_code: int = 400
    code: str = "bad-request"
    title: str = "Solicitud inválida"

    def __init__(self, detail: str | None = None, *, extra: dict[str, Any] | None = None):
        super().__init__(detail or self.title)
        self.detail = detail
        self.extra = extra or {}

    def response_headers(self) -> dict[str, str]:
        return {}


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"
    title = "No autenticado"

    def response_headers(self) -> dict[str, str]:
        return {"WWW-Authenticate": "Bearer"}


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"
    title = "Permisos insuficientes"


class NotFoundError(AppError):
    status_code = 404
    code = "not-found"
    title = "Recurso no encontrado"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    title = "Conflicto con el estado actual del recurso"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service-unavailable"
    title = "Servicio no disponible temporalmente"


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"{PROBLEM_TYPE_BASE}{code}",
        "title": title,
        "status": status,
        "instance": request.url.path,
    }
    if detail:
        body["detail"] = detail
    request_id = _request_id(request)
    if request_id:
        body["request_id"] = request_id
    if extra:
        body.update(extra)

    response_headers = dict(headers or {})
    if request_id:
        response_headers["X-Request-ID"] = request_id
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=response_headers)


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return problem_response(
        request,
        status=exc.status_code,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        extra=exc.extra,
        headers=exc.response_headers(),
    )


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    try:
        phrase = HTTPStatus(exc.status_code).phrase
    except ValueError:
        phrase = "Error"
    code = phrase.lower().replace(" ", "-")
    # Solo se expone el detalle si es un texto explícito distinto de la frase estándar.
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail != phrase else None
    return problem_response(
        request,
        status=exc.status_code,
        code=code,
        title=phrase,
        detail=detail,
        headers=dict(exc.headers) if exc.headers else None,
    )


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # No se devuelve el campo "input": podría reflejar datos sensibles enviados por el cliente.
    errors = [
        {
            "loc": list(error.get("loc", ())),
            # Pydantic antepone "Value error, " a los mensajes de validadores propios.
            "msg": str(error.get("msg", "")).removeprefix("Value error, "),
            "type": error.get("type", ""),
        }
        for error in exc.errors()
    ]
    return problem_response(
        request,
        status=422,
        code="validation-error",
        title="Los datos enviados no son válidos",
        extra={"errors": errors},
    )


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_error", path=request.url.path, error_type=type(exc).__name__)
    return problem_response(
        request,
        status=500,
        code="internal-error",
        title="Error interno del servidor",
        detail="Ocurrió un error inesperado. Indica el request_id al reportarlo.",
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
