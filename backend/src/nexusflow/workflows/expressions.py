"""Plantillas y condiciones: análisis y evaluación sin código ejecutable.

Plantillas: `{{ ruta }}` dentro de cualquier cadena de la configuración de un paso.
- Si la cadena es exactamente una plantilla, se sustituye por el valor con su tipo
  original (número, objeto…).
- Si la plantilla va dentro de un texto, el valor se convierte a texto.

Rutas válidas:
- `input.<campo>...`             entrada de la ejecución
- `steps.<id>.output...`         salida de un paso anterior
- `run.id`, `run.workflow_id`    metadatos de la ejecución
Los segmentos numéricos indexan listas: `steps.a.output.items.0.id`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import JsonValue

from nexusflow.workflows.dsl import AllOf, AnyOf, Comparison, Condition, NotOf

_SEGMENT = r"[A-Za-z0-9_\-]+"
PATH_RE = re.compile(rf"^{_SEGMENT}(?:\.{_SEGMENT})*$")
TEMPLATE_RE = re.compile(r"\{\{\s*(.*?)\s*\}\}")
_FULL_TEMPLATE_RE = re.compile(r"^\{\{\s*(.*?)\s*\}\}$")

ALLOWED_ROOTS = frozenset({"input", "steps", "run"})
RUN_FIELDS = frozenset({"id", "workflow_id"})
_MISSING = object()


class ExpressionError(ValueError):
    """Error al evaluar una plantilla o condición en tiempo de ejecución."""


@dataclass(frozen=True, slots=True)
class Reference:
    path: str
    location: str  # dónde aparece, para mensajes de validación

    @property
    def segments(self) -> list[str]:
        return self.path.split(".")


@dataclass(frozen=True, slots=True)
class MalformedTemplate:
    text: str
    location: str


# ------------------------------------------------------------------ análisis estático


def iter_strings(value: JsonValue, location: str) -> Iterator[tuple[str, str]]:
    """Recorre todas las cadenas de una estructura JSON (incluidas las claves)."""
    if isinstance(value, str):
        yield value, location
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield key, f"{location}.{key}"
            yield from iter_strings(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_strings(item, f"{location}[{index}]")


def scan_templates(
    value: JsonValue, location: str
) -> tuple[list[Reference], list[MalformedTemplate]]:
    references: list[Reference] = []
    malformed: list[MalformedTemplate] = []
    for text, where in iter_strings(value, location):
        for match in TEMPLATE_RE.finditer(text):
            path = match.group(1)
            if PATH_RE.fullmatch(path):
                references.append(Reference(path, where))
            else:
                malformed.append(MalformedTemplate(match.group(0), where))
        # "{{" sin cerrar, o llaves sueltas: casi siempre un error de escritura.
        if text.count("{{") != len(TEMPLATE_RE.findall(text)):
            malformed.append(MalformedTemplate(text[:80], where))
    return references, malformed


def condition_fields(condition: Condition, location: str) -> Iterator[Reference]:
    if isinstance(condition, Comparison):
        yield Reference(condition.field, f"{location}.field")
    elif isinstance(condition, AllOf):
        for i, sub in enumerate(condition.all):
            yield from condition_fields(sub, f"{location}.all[{i}]")
    elif isinstance(condition, AnyOf):
        for i, sub in enumerate(condition.any):
            yield from condition_fields(sub, f"{location}.any[{i}]")
    elif isinstance(condition, NotOf):
        yield from condition_fields(condition.not_, f"{location}.not")


# ------------------------------------------------------------------ evaluación


def resolve_path(context: Mapping[str, Any], path: str) -> Any:
    current: Any = context
    for segment in path.split("."):
        if isinstance(current, Mapping) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            return _MISSING
    return current


def _lookup(context: Mapping[str, Any], path: str) -> Any:
    value = resolve_path(context, path)
    if value is _MISSING:
        raise ExpressionError(f"La referencia '{path}' no existe en el contexto de ejecución")
    return value


def render(value: JsonValue, context: Mapping[str, Any]) -> JsonValue:
    """Sustituye las plantillas de una estructura JSON por valores del contexto."""
    if isinstance(value, str):
        full = _FULL_TEMPLATE_RE.fullmatch(value)
        if full and PATH_RE.fullmatch(full.group(1)):
            result: JsonValue = _lookup(context, full.group(1))
            return result

        def _substitute(match: re.Match[str]) -> str:
            found = _lookup(context, match.group(1))
            if isinstance(found, dict | list):
                return json.dumps(found, separators=(",", ":"), ensure_ascii=False)
            if found is None:
                return ""
            if isinstance(found, bool):
                return "true" if found else "false"
            return str(found)

        return TEMPLATE_RE.sub(_substitute, value)
    if isinstance(value, Mapping):
        return {render(k, context): render(v, context) for k, v in value.items()}  # type: ignore[misc]
    if isinstance(value, list):
        return [render(item, context) for item in value]
    return value


def _compare(op: str, left: Any, right: Any) -> bool:
    if op == "exists":
        return left is not _MISSING and left is not None
    if op == "not_exists":
        return left is _MISSING or left is None
    if left is _MISSING:
        # Un campo ausente no es igual a nada ni mayor que nada: la comparación es falsa.
        return op in {"ne", "not_in"}
    if op == "eq":
        return bool(left == right)
    if op == "ne":
        return bool(left != right)
    if op == "in":
        return left in right
    if op == "not_in":
        return left not in right
    if op == "contains":
        if isinstance(left, str) and isinstance(right, str):
            return right in left
        if isinstance(left, list):
            return right in left
        return False
    # Orden: solo entre números o entre cadenas; mezclar tipos es falso, no un error.
    comparable = (
        isinstance(left, int | float)
        and isinstance(right, int | float)
        and not isinstance(left, bool)
        and not isinstance(right, bool)
    ) or (isinstance(left, str) and isinstance(right, str))
    if not comparable:
        return False
    results: dict[str, bool] = {
        "gt": left > right,
        "gte": left >= right,
        "lt": left < right,
        "lte": left <= right,
    }
    return results[op]


def evaluate(condition: Condition, context: Mapping[str, Any]) -> bool:
    if isinstance(condition, Comparison):
        return _compare(condition.op, resolve_path(context, condition.field), condition.value)
    if isinstance(condition, AllOf):
        return all(evaluate(c, context) for c in condition.all)
    if isinstance(condition, AnyOf):
        return any(evaluate(c, context) for c in condition.any)
    return not evaluate(condition.not_, context)
