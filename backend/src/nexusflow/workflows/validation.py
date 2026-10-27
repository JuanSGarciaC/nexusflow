"""Validación semántica de una definición ya válida estructuralmente.

Pydantic garantiza la forma; aquí se comprueba el significado:
- identificadores únicos y transiciones a pasos existentes;
- grafo acíclico y todos los pasos alcanzables desde `start`;
- plantillas bien formadas que solo referencian entradas declaradas o pasos que se han
  ejecutado *con certeza* antes (dominadores en el grafo, no un simple ancestro: si el
  paso referenciado está en otra rama, podría no haberse ejecutado).

Un borrador puede guardarse con incidencias (para editarlo por partes), pero solo se
publica si no tiene ninguna.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from nexusflow.workflows.dsl import (
    ConditionStep,
    HttpRequestStep,
    TransformStep,
    WorkflowDefinition,
    step_successors,
)
from nexusflow.workflows.expressions import (
    ALLOWED_ROOTS,
    RUN_FIELDS,
    Reference,
    condition_fields,
    scan_templates,
)


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    message: str
    location: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "location": self.location}


def _dominators(start: str, graph: dict[str, list[str]], nodes: list[str]) -> dict[str, set[str]]:
    """Dominadores de cada nodo alcanzable (algoritmo iterativo; n ≤ 50)."""
    predecessors: dict[str, set[str]] = {n: set() for n in nodes}
    for node, successors in graph.items():
        for succ in successors:
            if succ in predecessors:
                predecessors[succ].add(node)
    dom: dict[str, set[str]] = {n: set(nodes) for n in nodes}
    dom[start] = {start}
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if node == start:
                continue
            preds = [dom[p] for p in predecessors[node] if p in dom]
            new = ({node} | set.intersection(*preds)) if preds else {node}
            if new != dom[node]:
                dom[node] = new
                changed = True
    return dom


def _reachable(start: str, graph: dict[str, list[str]]) -> list[str]:
    seen: list[str] = []
    stack = [start]
    while stack:
        node = stack.pop()
        if node in seen or node not in graph:
            continue
        seen.append(node)
        stack.extend(graph[node])
    return seen


def _find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    white, grey, black = 0, 1, 2
    color = dict.fromkeys(graph, white)
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = grey
        path.append(node)
        for succ in graph.get(node, []):
            if succ not in color:
                continue
            if color[succ] == grey:
                return [*path[path.index(succ) :], succ]
            if color[succ] == white and (cycle := visit(succ)):
                return cycle
        path.pop()
        color[node] = black
        return None

    for node in graph:
        if color[node] == white and (cycle := visit(node)):
            return cycle
    return None


def _step_references(step: object, index: int) -> tuple[list[Reference], list[Issue]]:
    location = f"steps[{index}].config"
    issues: list[Issue] = []
    references: list[Reference] = []
    if isinstance(step, HttpRequestStep):
        payload = step.config.model_dump(mode="json", exclude={"success_status", "credential"})
        refs, malformed = scan_templates(payload, location)
        references += refs
        issues += [
            Issue("malformed_template", f"Plantilla no válida: {m.text}", m.location)
            for m in malformed
        ]
    elif isinstance(step, TransformStep):
        refs, malformed = scan_templates(step.config.output, f"{location}.output")
        references += refs
        issues += [
            Issue("malformed_template", f"Plantilla no válida: {m.text}", m.location)
            for m in malformed
        ]
    elif isinstance(step, ConditionStep):
        references += list(condition_fields(step.config.when, f"{location}.when"))
    return references, issues


def validate_definition(definition: WorkflowDefinition) -> list[Issue]:
    issues: list[Issue] = []
    ids = [step.id for step in definition.steps]

    seen: set[str] = set()
    for index, step_id in enumerate(ids):
        if step_id in seen:
            issues.append(
                Issue("duplicate_step_id", f"El id '{step_id}' está repetido", f"steps[{index}].id")
            )
        seen.add(step_id)

    steps = definition.step_map()
    if definition.start not in steps:
        issues.append(
            Issue("unknown_start", f"El paso inicial '{definition.start}' no existe", "start")
        )

    graph: dict[str, list[str]] = {}
    for index, step in enumerate(definition.steps):
        successors = step_successors(step)
        for target in successors:
            if target not in steps:
                issues.append(
                    Issue(
                        "unknown_step",
                        f"El paso '{step.id}' apunta a '{target}', que no existe",
                        f"steps[{index}]",
                    )
                )
            elif target == step.id:
                issues.append(
                    Issue("self_loop", f"El paso '{step.id}' apunta a sí mismo", f"steps[{index}]")
                )
        graph[step.id] = [t for t in successors if t in steps]

    cycle = _find_cycle(graph)
    if cycle:
        issues.append(Issue("cycle", "El flujo contiene un ciclo: " + " → ".join(cycle), "steps"))

    if definition.start not in steps or cycle:
        return issues  # sin un DAG con inicio válido no tiene sentido seguir

    reachable = _reachable(definition.start, graph)
    for index, step in enumerate(definition.steps):
        if step.id not in reachable:
            issues.append(
                Issue(
                    "unreachable_step",
                    f"El paso '{step.id}' no es alcanzable desde '{definition.start}'",
                    f"steps[{index}]",
                )
            )

    dominators = _dominators(definition.start, graph, reachable)
    declared_inputs = {field.name for field in definition.inputs}

    for index, step in enumerate(definition.steps):
        references, template_issues = _step_references(step, index)
        issues += template_issues
        for ref in references:
            issues += _check_reference(ref, step.id, steps, dominators, declared_inputs)
    return issues


def _check_reference(
    ref: Reference,
    current: str,
    steps: Mapping[str, object],
    dominators: dict[str, set[str]],
    declared_inputs: set[str],
) -> list[Issue]:
    segments = ref.segments
    root = segments[0]
    if root not in ALLOWED_ROOTS:
        return [
            Issue(
                "invalid_reference",
                f"'{ref.path}' debe empezar por input., steps. o run.",
                ref.location,
            )
        ]
    if root == "input":
        if len(segments) < 2 or segments[1] not in declared_inputs:
            return [
                Issue(
                    "unknown_input",
                    f"'{ref.path}' usa una entrada no declarada en 'inputs'",
                    ref.location,
                )
            ]
        return []
    if root == "run":
        if len(segments) != 2 or segments[1] not in RUN_FIELDS:
            return [
                Issue(
                    "invalid_reference",
                    f"'{ref.path}' no es válido; usa run.id o run.workflow_id",
                    ref.location,
                )
            ]
        return []
    # steps.<id>.output...
    if len(segments) < 3 or segments[2] != "output":
        return [
            Issue(
                "invalid_reference",
                f"'{ref.path}' debe tener la forma steps.<id>.output...",
                ref.location,
            )
        ]
    target = segments[1]
    if target not in steps:
        return [Issue("unknown_step", f"'{ref.path}' referencia un paso inexistente", ref.location)]
    if target == current:
        return [Issue("invalid_reference", f"'{ref.path}' referencia al propio paso", ref.location)]
    if current in dominators and target not in dominators[current]:
        return [
            Issue(
                "reference_not_guaranteed",
                f"'{ref.path}': el paso '{target}' no se ejecuta siempre antes de '{current}'",
                ref.location,
            )
        ]
    return []
