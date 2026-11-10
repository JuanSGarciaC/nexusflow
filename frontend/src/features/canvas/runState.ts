/** Estado de una ejecución proyectado sobre el lienzo. Funciones puras. */
import type { Schemas } from "../../api/client";
import type { FlowEdge } from "./layout";

export type StepView = Schemas["StepView"];

export interface NodeRun {
  status: StepView["status"];
  attempts: number;
  maxAttempts: number;
  error: string | null;
  durationMs: number | null;
}

function errorText(error: StepView["error"]): string | null {
  if (!error) return null;
  const text = error.message ?? error.class;
  return typeof text === "string" ? text : null;
}

/** Clave: step_id. Un paso sin registro no aparece: el lienzo lo atenúa. */
export function nodeRuns(steps: StepView[]): Map<string, NodeRun> {
  const runs = new Map<string, NodeRun>();
  for (const step of steps) {
    const timed = step.started_at && step.finished_at;
    runs.set(step.step_id, {
      status: step.status,
      attempts: step.attempt_count,
      maxAttempts: step.max_attempts,
      error: errorText(step.error),
      durationMs: timed ? Date.parse(step.finished_at as string) - Date.parse(step.started_at as string) : null,
    });
  }
  return runs;
}

/** Conexiones cuyo origen y destino tienen registro y cuyo destino ya empezó. */
export function traversedEdges(edges: FlowEdge[], runs: Map<string, NodeRun>): Set<string> {
  const traversed = new Set<string>();
  for (const edge of edges) {
    const target = runs.get(edge.target);
    if (runs.has(edge.source) && target && target.status !== "pending") traversed.add(edge.id);
  }
  return traversed;
}
