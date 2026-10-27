"""Validación de la entrada de una ejecución contra las `inputs` declaradas en el flujo."""

from __future__ import annotations

from typing import Any

from nexusflow.core.errors import AppError
from nexusflow.workflows.dsl import InputField


class InvalidRunInputError(AppError):
    status_code = 422
    code = "invalid-run-input"
    title = "La entrada de la ejecución no es válida"

    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__("Revisa los campos indicados en 'errors'.", extra={"errors": errors})


def _matches(expected: str, value: Any) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    return isinstance(value, list)


def validate_run_input(
    fields: list[InputField], data: dict[str, Any], *, strict: bool = True
) -> dict[str, Any]:
    """Valida tipos y obligatorios. Con strict=False (webhooks entrantes) se ignoran los
    campos no declarados: un sistema externo suele enviar más datos de los necesarios."""
    errors: list[dict[str, str]] = []
    declared = {f.name: f for f in fields}
    if not strict:
        data = {k: v for k, v in data.items() if k in declared}
    for name in data:
        if name not in declared:
            errors.append({"field": name, "msg": "Campo no declarado en las entradas del flujo"})
    for field in fields:
        if field.name not in data or data[field.name] is None:
            if field.required:
                errors.append({"field": field.name, "msg": "Campo obligatorio"})
            continue
        if not _matches(field.type, data[field.name]):
            errors.append({"field": field.name, "msg": f"Debe ser de tipo {field.type}"})
    if errors:
        raise InvalidRunInputError(errors)
    return data
