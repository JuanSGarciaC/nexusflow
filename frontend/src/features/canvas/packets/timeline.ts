/**
 * Qué paquetes viajan y cuándo, a partir de los pasos reales de una ejecución. Funciones puras.
 * El orden sale de los datos (inicio de cada paso, o su secuencia); los tiempos se comprimen a
 * una reproducción corta: un paso de espera de horas no puede congelar el lienzo.
 */
import type { FlowEdge } from "../layout";
import { traversedEdges, type NodeRun, type StepView } from "../runState";

/** Qué ocurre al llegar: anillo verde, chispas rojas o anillo de espera. */
export type Arrival = "ok" | "fail" | "wait";

export interface PacketPlan {
  edgeId: string;
  /** ms desde el inicio de la reproducción. */
  at: number;
  /** Rebotes antes de llegar: uno por cada reintento (con tope). */
  bounces: number;
  arrival: Arrival;
}

export const REPLAY_MS = 3200;
const MAX_GAP = 560;
const MAX_BOUNCES = 3;
const WAITING = new Set<StepView["status"]>(["running", "retry_scheduled", "needs_review", "pending"]);

export function arrivalFor(run: NodeRun): Arrival {
  if (run.status === "failed") return "fail";
  return WAITING.has(run.status) ? "wait" : "ok";
}

export function bouncesFor(run: NodeRun): number {
  return Math.min(Math.max(run.attempts - 1, 0), MAX_BOUNCES);
}

/** Clave de orden de un paso: su inicio si lo tiene, si no su secuencia. */
function orderKey(step: StepView): number {
  const started = step.started_at ? Date.parse(step.started_at) : NaN;
  return Number.isFinite(started) ? started : step.sequence;
}

export function packetFor(edge: FlowEdge, runs: Map<string, NodeRun>, at = 0): PacketPlan | null {
  const run = runs.get(edge.target);
  return run ? { edgeId: edge.id, at, bounces: bouncesFor(run), arrival: arrivalFor(run) } : null;
}

/** Reproducción de la ejecución: un paquete por conexión recorrida, en el orden en que ocurrió. */
export function replayPlan(edges: FlowEdge[], steps: StepView[], runs: Map<string, NodeRun>): PacketPlan[] {
  const traversed = traversedEdges(edges, runs);
  // Un paso puede tener varios registros (bucles): cuenta el primero.
  const keys = new Map<string, number>();
  for (const step of steps) {
    const key = orderKey(step);
    const seen = keys.get(step.step_id);
    if (seen === undefined || key < seen) keys.set(step.step_id, key);
  }
  const walked = edges.filter((e) => traversed.has(e.id));
  const ranks = [...new Set(walked.map((e) => keys.get(e.target) ?? 0))].sort((a, b) => a - b);
  if (ranks.length === 0) return [];
  const gap = Math.min(MAX_GAP, REPLAY_MS / ranks.length);
  return walked
    .map((edge) => packetFor(edge, runs, ranks.indexOf(keys.get(edge.target) ?? 0) * gap))
    .filter((plan): plan is PacketPlan => plan !== null)
    .sort((a, b) => a.at - b.at);
}

/** Conexiones que llevan flujo continuo: su destino está trabajando o esperando reintento. */
export function streamingEdges(edges: FlowEdge[], runs: Map<string, NodeRun>): string[] {
  const traversed = traversedEdges(edges, runs);
  return edges
    .filter((e) => traversed.has(e.id))
    .filter((e) => {
      const status = runs.get(e.target)?.status;
      return status === "running" || status === "retry_scheduled";
    })
    .map((e) => e.id);
}

/** Conexiones recorridas ahora que no lo estaban en la consulta anterior. */
export function newlyTraversed(before: Set<string>, now: Set<string>): string[] {
  return [...now].filter((id) => !before.has(id));
}
