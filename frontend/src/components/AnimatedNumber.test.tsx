import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { mediaMatching } from "../test/fakeCanvas";
import { AnimatedNumber } from "./AnimatedNumber";

const fmt = (n: number | null) => (n === null ? "—" : String(Math.round(n)));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("sin movimiento muestra la cifra final al instante", () => {
  render(<AnimatedNumber value={42} format={fmt} />);
  expect(screen.getByText("42")).toBeInTheDocument();
});

test("con movimiento cuenta hasta la cifra y termina en el valor exacto", () => {
  vi.stubGlobal("matchMedia", mediaMatching("no-preference"));
  const frames: FrameRequestCallback[] = [];
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => frames.push(cb));
  vi.spyOn(performance, "now").mockReturnValue(0);
  const { container, rerender } = render(<AnimatedNumber value={100} format={fmt} />);
  // Antes del primer fotograma ya muestra el valor de partida, no el final.
  expect(container.textContent).toBe("0");
  frames.shift()?.(-5);
  expect(container.textContent).toBe("0");
  frames.shift()?.(350);
  const mid = Number(container.textContent);
  expect(mid).toBeGreaterThan(0);
  expect(mid).toBeLessThan(100);
  frames.shift()?.(700);
  expect(container.textContent).toBe("100");
  // Un cambio a mitad de recuento sigue desde la cifra anterior y termina en la nueva.
  rerender(<AnimatedNumber value={120} format={fmt} />);
  rerender(<AnimatedNumber value={130} format={fmt} />);
  expect(container.textContent).toBe("120");
  frames.at(-1)?.(5000);
  expect(container.textContent).toBe("130");
});

test("un valor nulo se muestra con su formato", () => {
  render(<AnimatedNumber value={null} format={fmt} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});
