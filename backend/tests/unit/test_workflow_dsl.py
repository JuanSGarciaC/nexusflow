"""DSL de flujos: estructura, plantillas, condiciones y validación del grafo."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from nexusflow.workflows.dsl import SideEffect, WorkflowDefinition, step_side_effect
from nexusflow.workflows.expressions import ExpressionError, evaluate, render, scan_templates
from nexusflow.workflows.validation import validate_definition

VALID: dict[str, Any] = {
    "inputs": [{"name": "order_id", "type": "string"}],
    "start": "fetch",
    "steps": [
        {
            "id": "fetch",
            "type": "http_request",
            "config": {
                "method": "GET",
                "url": "https://api.example.com/orders/{{ input.order_id }}",
            },
            "next": "is_paid",
        },
        {
            "id": "is_paid",
            "type": "condition",
            "config": {
                "when": {
                    "all": [
                        {"field": "steps.fetch.output.status_code", "op": "eq", "value": 200},
                        {"field": "steps.fetch.output.body.status", "op": "eq", "value": "paid"},
                    ]
                }
            },
            "on_true": "summary",
            "on_false": "wait",
        },
        {
            "id": "wait",
            "type": "delay",
            "config": {"seconds": 60},
        },
        {
            "id": "summary",
            "type": "transform",
            "config": {
                "output": {
                    "order": "{{ steps.fetch.output.body.id }}",
                    "text": "Pedido {{ input.order_id }} pagado",
                    "run": "{{ run.id }}",
                }
            },
        },
    ],
}


def _definition(data: dict[str, Any] | None = None) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(data or VALID)


def _codes(data: dict[str, Any]) -> list[str]:
    return [issue.code for issue in validate_definition(_definition(data))]


def _mutated() -> dict[str, Any]:
    return copy.deepcopy(VALID)


# ------------------------------------------------------------------ estructura


def test_valid_definition_has_no_issues() -> None:
    assert validate_definition(_definition()) == []


def test_unknown_step_type_rejected() -> None:
    data = _mutated()
    data["steps"][2] = {"id": "wait", "type": "shell", "config": {"cmd": "rm -rf /"}}
    with pytest.raises(ValidationError):
        _definition(data)


def test_unknown_fields_rejected() -> None:
    data = _mutated()
    data["steps"][0]["config"]["eval"] = "__import__('os')"
    with pytest.raises(ValidationError, match="Extra inputs"):
        _definition(data)


@pytest.mark.parametrize("header", ["Host", "Content-Length", "Idempotency-Key", "Bad Header"])
def test_dangerous_headers_rejected(header: str) -> None:
    data = _mutated()
    data["steps"][0]["config"]["headers"] = {header: "x"}
    with pytest.raises(ValidationError):
        _definition(data)


def test_header_injection_rejected() -> None:
    data = _mutated()
    data["steps"][0]["config"]["headers"] = {"X-Test": "a\r\nX-Evil: 1"}
    with pytest.raises(ValidationError):
        _definition(data)


def test_step_id_format_enforced() -> None:
    data = _mutated()
    data["steps"][0]["id"] = "Fetch Order"
    with pytest.raises(ValidationError):
        _definition(data)


def test_too_many_steps_rejected() -> None:
    data = {
        "start": "s0",
        "steps": [{"id": f"s{i}", "type": "delay", "config": {"seconds": 1}} for i in range(51)],
    }
    with pytest.raises(ValidationError):
        _definition(data)


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("GET", SideEffect.NONE),
        ("PUT", SideEffect.IDEMPOTENT),
        ("DELETE", SideEffect.IDEMPOTENT),
        ("POST", SideEffect.NON_IDEMPOTENT),
        ("PATCH", SideEffect.NON_IDEMPOTENT),
    ],
)
def test_side_effect_defaults_follow_http_semantics(method: str, expected: SideEffect) -> None:
    data = _mutated()
    data["steps"][0]["config"]["method"] = method
    assert step_side_effect(_definition(data).steps[0]) == expected


def test_declared_side_effect_overrides_default() -> None:
    data = _mutated()
    data["steps"][0]["config"]["method"] = "POST"
    data["steps"][0]["side_effect"] = "idempotent"
    assert step_side_effect(_definition(data).steps[0]) == SideEffect.IDEMPOTENT


# ------------------------------------------------------------------ grafo


def test_cycle_detected() -> None:
    data = _mutated()
    data["steps"][3]["next"] = "fetch"
    assert "cycle" in _codes(data)


def test_self_loop_detected() -> None:
    data = _mutated()
    data["steps"][2]["next"] = "wait"
    assert "self_loop" in _codes(data)


def test_unknown_targets_detected() -> None:
    data = _mutated()
    data["steps"][0]["next"] = "ghost"
    data["start"] = "nowhere"
    codes = _codes(data)
    assert "unknown_step" in codes
    assert "unknown_start" in codes


def test_unreachable_step_detected() -> None:
    data = _mutated()
    data["steps"].append({"id": "orphan", "type": "delay", "config": {"seconds": 1}})
    assert _codes(data) == ["unreachable_step"]


def test_duplicate_ids_detected() -> None:
    data = _mutated()
    data["steps"][2]["id"] = "summary"
    data["steps"][1]["on_false"] = None
    assert "duplicate_step_id" in _codes(data)


# ------------------------------------------------------------------ referencias


def test_reference_to_undeclared_input() -> None:
    data = _mutated()
    data["inputs"] = []
    assert "unknown_input" in _codes(data)


def test_reference_to_step_in_other_branch_is_not_guaranteed() -> None:
    data = _mutated()
    # 'wait' solo se ejecuta en la rama falsa: 'summary' no puede depender de él.
    data["steps"][3]["config"]["output"]["bad"] = "{{ steps.wait.output.x }}"
    assert _codes(data) == ["reference_not_guaranteed"]


def test_reference_to_later_step_rejected() -> None:
    data = _mutated()
    data["steps"][0]["config"]["url"] = "https://x.example.com/{{ steps.summary.output.order }}"
    assert "reference_not_guaranteed" in _codes(data)


@pytest.mark.parametrize(
    ("template", "code"),
    [
        ("{{ secrets.token }}", "invalid_reference"),
        ("{{ run.started_at }}", "invalid_reference"),
        ("{{ steps.fetch.body }}", "invalid_reference"),
        ("{{ steps.nope.output.x }}", "unknown_step"),
        ("{{ __import__('os') }}", "malformed_template"),
        ("{{ input.order_id ", "malformed_template"),
    ],
)
def test_invalid_templates(template: str, code: str) -> None:
    data = _mutated()
    data["steps"][3]["config"]["output"]["x"] = template
    assert code in _codes(data)


def test_scan_finds_templates_in_keys_and_nested_values() -> None:
    refs, malformed = scan_templates(
        {"{{ input.a }}": [{"b": "x {{ steps.s.output.c }} y"}]}, "body"
    )
    assert [r.path for r in refs] == ["input.a", "steps.s.output.c"]
    assert malformed == []


# ------------------------------------------------------------------ evaluación


CONTEXT: dict[str, Any] = {
    "input": {"order_id": "A-1", "qty": 3},
    "steps": {
        "fetch": {"output": {"status_code": 200, "body": {"items": [{"id": 7}], "ok": True}}}
    },
    "run": {"id": "r1", "workflow_id": "w1"},
}


def test_render_preserves_types_for_full_templates() -> None:
    assert render("{{ input.qty }}", CONTEXT) == 3
    assert render("{{ steps.fetch.output.body.items }}", CONTEXT) == [{"id": 7}]
    assert render("{{ steps.fetch.output.body.items.0.id }}", CONTEXT) == 7


def test_render_interpolates_inside_text() -> None:
    assert render("Pedido {{ input.order_id }} x{{ input.qty }}", CONTEXT) == "Pedido A-1 x3"
    assert render("ok={{ steps.fetch.output.body.ok }}", CONTEXT) == "ok=true"
    assert render({"k": ["{{ run.id }}"]}, CONTEXT) == {"k": ["r1"]}


def test_render_missing_reference_raises() -> None:
    with pytest.raises(ExpressionError, match=r"input\.missing"):
        render("{{ input.missing }}", CONTEXT)


def test_render_does_not_reinterpret_values() -> None:
    # Un valor que contiene "{{ ... }}" se inserta tal cual: no hay evaluación recursiva.
    context = {"input": {"evil": "{{ run.id }}"}}
    assert render("{{ input.evil }}", context) == "{{ run.id }}"


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ({"field": "input.qty", "op": "gt", "value": 2}, True),
        ({"field": "input.qty", "op": "lte", "value": 2}, False),
        ({"field": "input.qty", "op": "gt", "value": "2"}, False),  # tipos distintos
        ({"field": "input.order_id", "op": "contains", "value": "-"}, True),
        ({"field": "input.order_id", "op": "in", "value": ["A-1", "B-2"]}, True),
        ({"field": "input.nope", "op": "exists"}, False),
        ({"field": "input.nope", "op": "not_exists"}, True),
        ({"field": "input.nope", "op": "eq", "value": None}, False),
        ({"field": "input.nope", "op": "ne", "value": 1}, True),
        ({"not": {"field": "input.qty", "op": "eq", "value": 3}}, False),
        (
            {
                "any": [
                    {"field": "input.qty", "op": "eq", "value": 0},
                    {"field": "steps.fetch.output.status_code", "op": "eq", "value": 200},
                ]
            },
            True,
        ),
    ],
)
def test_condition_evaluation(condition: dict[str, Any], expected: bool) -> None:
    data = _mutated()
    data["steps"][1]["config"]["when"] = condition
    parsed = _definition(data).steps[1]
    assert evaluate(parsed.config.when, CONTEXT) is expected  # type: ignore[union-attr]


def test_in_operator_requires_list() -> None:
    data = _mutated()
    data["steps"][1]["config"]["when"] = {"field": "input.qty", "op": "in", "value": 3}
    with pytest.raises(ValidationError, match="lista"):
        _definition(data)
