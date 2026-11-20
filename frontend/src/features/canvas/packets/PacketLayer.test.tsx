import { MantineProvider } from "@mantine/core";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { fakeContext, mediaMatching } from "../../../test/fakeCanvas";
import FlowCanvas from "../FlowCanvas";
import type { FlowDefinition } from "../layout";
import type { StepView } from "../runState";

const DEFINITION = {
  schema_version: 1,
  start: "a",
  inputs: {},
  steps: [
    { id: "a", type: "transform", config: {}, next: "b" },
    { id: "b", type: "transform", config: {}, next: null },
  ],
} as unknown as FlowDefinition;

const step = (step_id: string, status: string, sequence: number) =>
  ({ step_id, status, sequence, attempt_count: 1, max_attempts: 1, error: null, started_at: null, finished_at: null }) as unknown as StepView;

let observers: IntersectionObserverCallback[] = [];

beforeEach(() => {
  observers = [];
  vi.stubGlobal("matchMedia", mediaMatching("no-preference"));
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(cb: IntersectionObserverCallback) {
        observers.push(cb);
      }
      observe() {}
      disconnect() {}
    },
  );
  const { ctx } = fakeContext();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as never);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function show(steps: StepView[] | undefined) {
  return render(
    <MantineProvider>
      <FlowCanvas definition={DEFINITION} steps={steps} />
    </MantineProvider>,
  );
}

test("en modo ejecución monta la capa de paquetes y permite repetir el recorrido", async () => {
  const raf = vi.spyOn(window, "requestAnimationFrame");
  const { rerender } = show([step("a", "succeeded", 1), step("b", "running", 2)]);
  expect(screen.getByTestId("packet-layer")).toHaveAttribute("aria-hidden");
  expect(raf).toHaveBeenCalled();

  // Fuera de pantalla se pausa; al volver, sigue.
  act(() => observers.forEach((cb) => cb([{ isIntersecting: false } as IntersectionObserverEntry], {} as IntersectionObserver)));
  act(() => observers.forEach((cb) => cb([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver)));
  act(() => document.dispatchEvent(new Event("visibilitychange")));

  fireEvent.click(screen.getByRole("button", { name: "Repetir recorrido" }));
  rerender(
    <MantineProvider>
      <FlowCanvas definition={DEFINITION} steps={[step("a", "succeeded", 1), step("b", "succeeded", 2)]} />
    </MantineProvider>,
  );
  expect(screen.getByTestId("packet-layer")).toBeInTheDocument();
});

test("sin ejecución, o con movimiento reducido, no hay capa", () => {
  show(undefined);
  expect(screen.queryByTestId("packet-layer")).toBeNull();
  // Ninguna consulta coincide: el navegador pide movimiento reducido.
  vi.stubGlobal("matchMedia", mediaMatching());
  show([step("a", "succeeded", 1)]);
  expect(screen.queryAllByTestId("packet-layer")).toHaveLength(0);
});
