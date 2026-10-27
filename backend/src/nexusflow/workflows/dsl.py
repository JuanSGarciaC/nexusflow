"""Lenguaje de definición de flujos (DSL), validado con Pydantic.

Un flujo es un grafo dirigido acíclico de pasos. Cada paso tiene un tipo cerrado:

- `http_request`: llama a una API REST externa.
- `transform`: construye un objeto a partir de la entrada y de salidas anteriores.
- `condition`: bifurca según una condición estructurada.
- `delay`: espera un tiempo antes de continuar.

Decisión de seguridad: no hay expresiones ejecutables. Las condiciones son JSON con un
conjunto cerrado de operadores y las plantillas solo admiten rutas (`{{ steps.a.output.id }}`),
sin llamadas, aritmética ni filtros. Así se elimina la inyección de código por diseño.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

MAX_STEPS = 50
STEP_ID_PATTERN = r"^[a-z][a-z0-9_]{0,62}$"
StepId = Annotated[str, StringConstraints(pattern=STEP_ID_PATTERN)]
InputName = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")]
FieldPath = Annotated[str, StringConstraints(min_length=1, max_length=256)]

# Cabeceras que el motor controla o que permitirían manipular la conexión.
FORBIDDEN_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "upgrade",
        "proxy-authorization",
        "te",
        "trailer",
        "keep-alive",
        "idempotency-key",
    }
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SideEffect(StrEnum):
    """Qué ocurre si un paso se ejecuta dos veces. Determina si es seguro reintentarlo."""

    NONE = "none"  # sin efectos externos (transform, condition, GET)
    IDEMPOTENT = "idempotent"  # repetirlo deja el mismo estado (PUT, DELETE)
    NON_IDEMPOTENT = "non_idempotent"  # repetirlo duplica el efecto (POST de un pago)


class RetryPolicy(_Strict):
    max_attempts: int = Field(default=1, ge=1, le=10)
    initial_backoff_seconds: float = Field(default=1.0, ge=0.1, le=300)
    backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10)
    max_backoff_seconds: float = Field(default=60.0, ge=1, le=3600)


# ------------------------------------------------------------------ condiciones

ComparisonOp = Literal[
    "eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "exists", "not_exists"
]


class Comparison(_Strict):
    field: FieldPath
    op: ComparisonOp
    value: JsonValue = None

    @model_validator(mode="after")
    def _value_shape(self) -> Self:
        if self.op in {"in", "not_in"} and not isinstance(self.value, list):
            raise ValueError(f"El operador '{self.op}' requiere una lista en 'value'")
        return self


class AllOf(_Strict):
    all: list[Condition] = Field(min_length=1, max_length=20)


class AnyOf(_Strict):
    any: list[Condition] = Field(min_length=1, max_length=20)


class NotOf(_Strict):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    not_: Condition = Field(alias="not")


Condition = Comparison | AllOf | AnyOf | NotOf


# ------------------------------------------------------------------ pasos


class _StepBase(_Strict):
    id: StepId
    name: str | None = Field(default=None, max_length=100)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    retry: RetryPolicy = RetryPolicy()


class HttpRequestConfig(_Strict):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    url: str = Field(min_length=1, max_length=2048)
    headers: dict[str, str] = Field(default_factory=dict, max_length=30)
    query: dict[str, str] = Field(default_factory=dict, max_length=30)
    body: JsonValue = None
    # Códigos considerados éxito. Por defecto, cualquier 2xx.
    success_status: list[int] | None = Field(default=None, max_length=20)
    # Nombre de una credencial guardada (Fase 5). Nunca el secreto en sí.
    credential: str | None = Field(default=None, max_length=100)

    @field_validator("headers")
    @classmethod
    def _safe_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        for name, value in headers.items():
            if not name or len(name) > 100 or not all(c.isalnum() or c in "-_" for c in name):
                raise ValueError(f"Nombre de cabecera no válido: {name!r}")
            if name.lower() in FORBIDDEN_HEADERS:
                raise ValueError(f"La cabecera '{name}' la gestiona el motor y no se permite")
            if "\r" in value or "\n" in value or len(value) > 4096:
                raise ValueError(f"Valor no válido en la cabecera '{name}'")
        return headers

    @field_validator("success_status")
    @classmethod
    def _valid_status(cls, codes: list[int] | None) -> list[int] | None:
        if codes is not None and any(not 100 <= c <= 599 for c in codes):
            raise ValueError("Los códigos de estado deben estar entre 100 y 599")
        return codes


class HttpRequestStep(_StepBase):
    type: Literal["http_request"]
    config: HttpRequestConfig
    # None = se deduce del método HTTP (ver effective_side_effect).
    side_effect: SideEffect | None = None
    next: StepId | None = None

    @property
    def effective_side_effect(self) -> SideEffect:
        if self.side_effect is not None:
            return self.side_effect
        # Semántica HTTP (RFC 9110): GET es seguro; PUT y DELETE son idempotentes.
        # POST y PATCH se tratan como no idempotentes salvo que se declare lo contrario.
        return {
            "GET": SideEffect.NONE,
            "PUT": SideEffect.IDEMPOTENT,
            "DELETE": SideEffect.IDEMPOTENT,
        }.get(self.config.method, SideEffect.NON_IDEMPOTENT)


class TransformConfig(_Strict):
    output: dict[str, JsonValue] = Field(min_length=1, max_length=50)


class TransformStep(_StepBase):
    type: Literal["transform"]
    config: TransformConfig
    next: StepId | None = None


class DelayConfig(_Strict):
    seconds: int = Field(ge=1, le=86_400)


class DelayStep(_StepBase):
    type: Literal["delay"]
    config: DelayConfig
    next: StepId | None = None


class ConditionConfig(_Strict):
    when: Condition


class ConditionStep(_StepBase):
    type: Literal["condition"]
    config: ConditionConfig
    on_true: StepId | None = None
    on_false: StepId | None = None


Step = Annotated[
    HttpRequestStep | TransformStep | DelayStep | ConditionStep, Field(discriminator="type")
]


def step_side_effect(step: Step) -> SideEffect:
    if isinstance(step, HttpRequestStep):
        return step.effective_side_effect
    return SideEffect.NONE


def step_successors(step: Step) -> list[str]:
    if isinstance(step, ConditionStep):
        return [s for s in (step.on_true, step.on_false) if s is not None]
    return [step.next] if step.next is not None else []


# ------------------------------------------------------------------ definición


class InputField(_Strict):
    name: InputName
    type: Literal["string", "number", "integer", "boolean", "object", "array"] = "string"
    required: bool = True
    description: str | None = Field(default=None, max_length=500)


class WorkflowDefinition(_Strict):
    schema_version: Literal[1] = 1
    inputs: list[InputField] = Field(default_factory=list, max_length=50)
    start: StepId
    steps: list[Step] = Field(min_length=1, max_length=MAX_STEPS)

    def step_map(self) -> dict[str, Step]:
        return {step.id: step for step in self.steps}


for _model in (AllOf, AnyOf, NotOf, ConditionConfig):
    _model.model_rebuild()
