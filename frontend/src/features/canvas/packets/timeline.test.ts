import { expect, test } from "vitest";

import type { FlowEdge } from "../layout";
import { nodeRuns, type StepView } from "../runState";
import { REPLAY_MS, arrivalFor, bouncesFor, newlyTraversed, replayPlan, streamingEdges } from "./timeline";

const EDGES: FlowEdge[] = [
  { id: "a->b:next", source: "a", target: "b", kind: "next" },
  { id: "b->c:true", source: "b", target: "c", kind: "true" },
  { id: "b->d:false", source: "b", target: "d", kind: "false" },
];

function step(step_id: string, status: string, extra: Partial<StepView> = {}): StepView {
  return { step_id, status, attempt_count: 1, max_attempts: 3, error: null, started_at: null, finished_at: null, sequence: 0, ...extra } as unknown as StepView;
}

test("la reproducción sigue el orden real de inicio y solo la rama tomada", () => {
  const steps = [
    step("a", "succeeded", { started_at: "2026-11-20T10:00:00Z", sequence: 1 }),
    step("b", "succeeded", { started_at: "2026-11-20T10:00:05Z", sequence: 2 }),
    step("c", "failed", { started_at: "2026-11-20T12:00:00Z", sequence: 3, attempt_count: 3 }),
  ];
  const plan = replayPlan(EDGES, steps, nodeRuns(steps));
  expect(plan.map((p) => p.edgeId)).toEqual(["a->b:next", "b->c:true"]);
  expect(plan[0]).toMatchObject({ at: 0, arrival: "ok", bounces: 0 });
  // Dos horas de espera no se reproducen: el hueco entre paquetes está acotado.
  expect(plan[1]?.at).toBeGreaterThan(0);
  expect(plan[1]?.at).toBeLessThanOrEqual(REPLAY_MS);
  expect(plan[1]).toMatchObject({ arrival: "fail", bounces: 2 });
});

test("sin marcas de tiempo ordena por secuencia", () => {
  const steps = [step("c", "succeeded", { sequence: 3 }), step("a", "succeeded", { sequence: 1 }), step("b", "succeeded", { sequence: 2 })];
  const plan = replayPlan(EDGES, steps, nodeRuns(steps));
  expect(plan.map((p) => p.edgeId)).toEqual(["a->b:next", "b->c:true"]);
});

test("una ejecución sin conexiones recorridas no reproduce nada", () => {
  const steps = [step("a", "running")];
  expect(replayPlan(EDGES, steps, nodeRuns(steps))).toEqual([]);
});

test("llegadas y rebotes según el estado del paso", () => {
  const run = { status: "running", attempts: 1, maxAttempts: 3, error: null, durationMs: null } as const;
  expect(arrivalFor(run)).toBe("wait");
  expect(arrivalFor({ ...run, status: "needs_review" })).toBe("wait");
  expect(arrivalFor({ ...run, status: "succeeded" })).toBe("ok");
  expect(arrivalFor({ ...run, status: "failed" })).toBe("fail");
  expect(bouncesFor({ ...run, attempts: 9 })).toBe(3);
  expect(bouncesFor({ ...run, attempts: 0 })).toBe(0);
});

test("fluye sin parar hacia el paso en curso o esperando reintento", () => {
  const steps = [step("a", "succeeded"), step("b", "succeeded"), step("c", "retry_scheduled", { attempt_count: 2 })];
  expect(streamingEdges(EDGES, nodeRuns(steps))).toEqual(["b->c:true"]);
  const done = [step("a", "succeeded"), step("b", "succeeded")];
  expect(streamingEdges(EDGES, nodeRuns(done))).toEqual([]);
});

test("detecta las conexiones recorridas desde la consulta anterior", () => {
  expect(newlyTraversed(new Set(["x"]), new Set(["x", "y"]))).toEqual(["y"]);
  expect(newlyTraversed(new Set(["x"]), new Set(["x"]))).toEqual([]);
});
