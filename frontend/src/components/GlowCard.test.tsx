import { fireEvent, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";

import { GlowCard } from "./GlowCard";

function show(ui: React.ReactNode) {
  return render(<MantineProvider>{ui}</MantineProvider>);
}

test("el foco sigue al cursor mediante variables CSS", () => {
  show(<GlowCard>contenido</GlowCard>);
  const card = screen.getByTestId("glow-card");
  fireEvent.pointerMove(card, { clientX: 40, clientY: 20 });
  expect(card.style.getPropertyValue("--mx")).toBe("40px");
  expect(card.style.getPropertyValue("--my")).toBe("20px");
});

test("sin tilt no se escribe la inclinación", () => {
  show(<GlowCard>x</GlowCard>);
  const card = screen.getByTestId("glow-card");
  fireEvent.pointerMove(card, { clientX: 40, clientY: 20 });
  expect(card.style.getPropertyValue("--rx")).toBe("");
});

test("con tilt la inclinación queda entre -4 y 4 grados y se limpia al salir", () => {
  show(<GlowCard tilt>x</GlowCard>);
  const card = screen.getByTestId("glow-card");
  card.getBoundingClientRect = () => ({ left: 0, top: 0, width: 200, height: 100 }) as DOMRect;
  fireEvent.pointerMove(card, { clientX: 200, clientY: 0 });
  expect(card.style.getPropertyValue("--ry")).toBe("4deg");
  expect(card.style.getPropertyValue("--rx")).toBe("4deg");
  fireEvent.pointerLeave(card);
  expect(card.style.getPropertyValue("--rx")).toBe("");
});

test("lleva data-motion para que la regla de movimiento reducido lo detenga", () => {
  show(<GlowCard>x</GlowCard>);
  expect(screen.getByTestId("glow-card")).toHaveAttribute("data-motion");
});
