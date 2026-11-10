import { expect, test } from "vitest";

import type { FlowEdge } from "./layout";
import { nodeRuns, traversedEdges, type StepView } from "./runState";

const step = (step_id: string, status: StepView["status"], extra: Partial<StepView> = {}) =>
  ({ step_id, status, attempt_count: 1, max_attempts: 3, error: null, started_at: null, finished_at: null, sequence: 1, ...extra }) as StepView;

const EDGES: FlowEdge[] = [
  { id: "c->si:true", source: "c", target: "si", kind: "true" },
  { id: "c->no:false", source: "c", target: "no", kind: "false" },
  { id: "si->fin:next", source: "si", target: "fin", kind: "next" },
];

test("nodeRuns copia estado e intentos y calcula la duración", () => {
  const runs = nodeRuns([
    step("a", "failed", {
      attempt_count: 3,
      started_at: "2026-11-09T10:00:00Z",
      finished_at: "2026-11-09T10:00:01.500Z",
      error: { class: "http_503", message: "Service Unavailable" },
    }),
  ]);
  expect(runs.get("a")).toEqual({ status: "failed", attempts: 3, maxAttempts: 3, error: "Service Unavailable", durationMs: 1500 });
});

test("sin inicio o sin fin la duración es null, y el error sin mensaje usa su clase", () => {
  const runs = nodeRuns([step("a", "running", { started_at: "2026-11-09T10:00:00Z", error: { class: "timeout" } })]);
  expect(runs.get("a")).toMatchObject({ durationMs: null, error: "timeout" });
});

test("solo cuenta como recorrida la rama que tiene registro", () => {
  const runs = nodeRuns([step("c", "succeeded"), step("si", "succeeded")]);
  expect([...traversedEdges(EDGES, runs)]).toEqual(["c->si:true"]);
});

test("un destino pendiente no cuenta como recorrido", () => {
  const runs = nodeRuns([step("c", "succeeded"), step("si", "succeeded"), step("fin", "pending")]);
  expect(traversedEdges(EDGES, runs).has("si->fin:next")).toBe(false);
});

test("sin pasos devuelve mapa y conjunto vacíos", () => {
  expect(nodeRuns([]).size).toBe(0);
  expect(traversedEdges(EDGES, nodeRuns([])).size).toBe(0);
});
