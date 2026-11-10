/**
 * Disposición del lienzo: convierte la definición de un flujo en nodos con posición y
 * conexiones. Función pura. Acepta borradores inválidos (destinos inexistentes, ciclos,
 * JSON a medio escribir): dibuja lo que puede y nunca lanza.
 */
import type { Schemas } from "../../api/client";

export type FlowDefinition = Schemas["WorkflowDefinition-Output"];
export type EdgeKind = "next" | "true" | "false";

export interface LaidOutNode {
  id: string;
  type: string;
  label: string;
  level: number;
  x: number;
  y: number;
  /** false si no hay camino desde `start`: el validador lo marcará como error. */
  reachable: boolean;
}

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
  kind: EdgeKind;
}

export const COL_W = 280;
export const ROW_H = 130;

interface RawStep {
  id: string;
  type?: unknown;
  name?: unknown;
  next?: unknown;
  on_true?: unknown;
  on_false?: unknown;
}

function isStep(value: unknown): value is RawStep {
  return typeof value === "object" && value !== null && typeof (value as RawStep).id === "string";
}

function successors(step: RawStep): [EdgeKind, unknown][] {
  return step.type === "condition"
    ? [["true", step.on_true], ["false", step.on_false]]
    : [["next", step.next]];
}

export function layoutFlow(def: FlowDefinition): { nodes: LaidOutNode[]; edges: FlowEdge[] } {
  const rawSteps: unknown = (def as { steps?: unknown } | null)?.steps;
  const steps = new Map<string, RawStep>();
  for (const step of Array.isArray(rawSteps) ? rawSteps : []) {
    if (isStep(step) && !steps.has(step.id)) steps.set(step.id, step);
  }

  const edges: FlowEdge[] = [];
  const out = new Map<string, FlowEdge[]>();
  for (const step of steps.values()) {
    for (const [kind, target] of successors(step)) {
      if (typeof target !== "string" || !steps.has(target)) continue;
      const edge = { id: `${step.id}->${target}:${kind}`, source: step.id, target, kind };
      edges.push(edge);
      out.set(step.id, [...(out.get(step.id) ?? []), edge]);
    }
  }

  // Recorrido en profundidad desde `start`: orden de descubrimiento, postorden y conexiones
  // de retorno (las que cierran un ciclo), que se ignoran al calcular niveles.
  const discovered: string[] = [];
  const postorder: string[] = [];
  const back = new Set<string>();
  const state = new Map<string, "open" | "done">();
  const visit = (id: string) => {
    state.set(id, "open");
    discovered.push(id);
    for (const edge of out.get(id) ?? []) {
      const seen = state.get(edge.target);
      if (seen === "open") back.add(edge.id);
      else if (seen === undefined) visit(edge.target);
    }
    state.set(id, "done");
    postorder.push(id);
  };
  const start: unknown = (def as { start?: unknown } | null)?.start;
  if (typeof start === "string" && steps.has(start)) visit(start);

  // Nivel = camino más largo desde `start`, relajando en orden topológico.
  const level = new Map<string, number>(discovered.map((id) => [id, 0]));
  for (const id of postorder.toReversed()) {
    for (const edge of out.get(id) ?? []) {
      if (back.has(edge.id)) continue;
      level.set(edge.target, Math.max(level.get(edge.target) ?? 0, (level.get(id) ?? 0) + 1));
    }
  }

  const orphanLevel = Math.max(-1, ...level.values()) + 1;
  const ordered = [...discovered, ...[...steps.keys()].filter((id) => !level.has(id))];
  const rows = new Map<number, number>();
  const nodes = ordered
    .map((id) => ({ id, level: level.get(id) ?? orphanLevel, reachable: level.has(id) }))
    .sort((a, b) => a.level - b.level)
    .map(({ id, level: lvl, reachable }): LaidOutNode => {
      const step = steps.get(id) as RawStep;
      const row = rows.get(lvl) ?? 0;
      rows.set(lvl, row + 1);
      return {
        id,
        type: typeof step.type === "string" ? step.type : "unknown",
        label: typeof step.name === "string" && step.name ? step.name : id,
        level: lvl,
        x: lvl * COL_W,
        y: row * ROW_H,
        reachable,
      };
    });

  return { nodes, edges };
}

/** Ids de nodos y conexiones en cualquier camino que pasa por `stepId`. */
export function connectedPath(edges: FlowEdge[], stepId: string): { nodes: Set<string>; edges: Set<string> } {
  const walk = (from: "source" | "target", to: "source" | "target") => {
    const seen = new Set<string>([stepId]);
    const queue = [stepId];
    while (queue.length > 0) {
      const current = queue.pop() as string;
      for (const edge of edges) {
        if (edge[from] === current && !seen.has(edge[to])) {
          seen.add(edge[to]);
          queue.push(edge[to]);
        }
      }
    }
    return seen;
  };
  const down = walk("source", "target");
  const up = walk("target", "source");
  const onPath = edges.filter(
    (e) => (down.has(e.source) && down.has(e.target)) || (up.has(e.source) && up.has(e.target)),
  );
  return { nodes: new Set([...up, ...down]), edges: new Set(onPath.map((e) => e.id)) };
}
