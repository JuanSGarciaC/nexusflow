import { MantineProvider } from "@mantine/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import FlowCanvas from "./FlowCanvas";
import type { FlowDefinition } from "./layout";
import type { StepView } from "./runState";

const DEFINITION = {
  schema_version: 1,
  start: "fetch_order",
  inputs: {},
  steps: [
    { id: "fetch_order", type: "http_request", config: { method: "GET", url: "https://x.test" }, next: "is_paid" },
    { id: "is_paid", type: "condition", config: { when: { field: "a", op: "eq", value: 1 } }, on_true: "cobro", on_false: null },
    { id: "cobro", type: "transform", config: { output: { ok: true } }, next: null },
  ],
} as unknown as FlowDefinition;

function show(ui: React.ReactNode) {
  return render(<MantineProvider>{ui}</MantineProvider>);
}

test("dibuja un nodo por paso", () => {
  show(<FlowCanvas definition={DEFINITION} />);
  expect(screen.getByRole("button", { name: "Paso fetch_order" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Paso is_paid" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Paso cobro" })).toBeInTheDocument();
});

test("en modo ejecución el nombre accesible incluye estado e intentos", () => {
  const steps = [
    { step_id: "fetch_order", status: "succeeded", attempt_count: 1, max_attempts: 3, error: null, started_at: null, finished_at: null },
    { step_id: "is_paid", status: "succeeded", attempt_count: 1, max_attempts: 1, error: null, started_at: null, finished_at: null },
    { step_id: "cobro", status: "failed", attempt_count: 3, max_attempts: 3, error: { message: "503" }, started_at: null, finished_at: null },
  ] as unknown as StepView[];
  show(<FlowCanvas definition={DEFINITION} steps={steps} />);
  expect(screen.getByRole("button", { name: "Paso cobro: fallida, 3 de 3 intentos" })).toBeInTheDocument();
});

test("los nodos no usan data-status: ese atributo identifica la insignia de estado", () => {
  const steps = [
    { step_id: "fetch_order", status: "succeeded", attempt_count: 1, max_attempts: 3, error: null, started_at: null, finished_at: null },
  ] as unknown as StepView[];
  const { container } = show(<FlowCanvas definition={DEFINITION} steps={steps} />);
  expect(container.querySelector("[data-status]")).toBeNull();
  expect(container.querySelector('[data-run="succeeded"]')).not.toBeNull();
});

test("un paso sin registro en la ejecución se anuncia como no ejecutado", () => {
  show(<FlowCanvas definition={DEFINITION} steps={[]} />);
  expect(screen.getByRole("button", { name: "Paso cobro: no ejecutado" })).toBeInTheDocument();
});

test("Enter sobre un nodo selecciona el paso", () => {
  const onSelectStep = vi.fn();
  show(<FlowCanvas definition={DEFINITION} onSelectStep={onSelectStep} />);
  fireEvent.keyDown(screen.getByRole("button", { name: "Paso is_paid" }), { key: "Enter" });
  expect(onSelectStep).toHaveBeenCalledWith("is_paid");
});

test("los disparadores aparecen como nodos de entrada", () => {
  show(<FlowCanvas definition={DEFINITION} triggers={[{ id: "manual", label: "Manual", kind: "manual" }]} />);
  expect(screen.getByLabelText("Disparador Manual")).toBeInTheDocument();
});

test("una definición sin pasos lo dice y no lanza", () => {
  show(<FlowCanvas definition={{ schema_version: 1, start: "", steps: [], inputs: {} } as unknown as FlowDefinition} />);
  expect(screen.getByText("Este flujo aún no tiene pasos")).toBeInTheDocument();
});
