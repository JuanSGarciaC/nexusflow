import { expect, test } from "vitest";

import { pointAt, sample } from "./geometry";

// Una L: 100 px a la derecha y 50 hacia abajo.
const L = (d: number) => (d <= 100 ? { x: d, y: 0 } : { x: 100, y: d - 100 });

test("muestrea un trazo y conserva su longitud", () => {
  const line = sample(150, L);
  // La esquina se recorta un poco entre muestras: unos píxeles de menos.
  expect(Math.abs(line.length - 150)).toBeLessThan(4);
  expect(line.xs.length).toBeGreaterThan(20);
});

test("interpola puntos y acota los extremos", () => {
  const line = sample(150, L);
  expect(pointAt(line, 50).x).toBeCloseTo(50, 0);
  expect(pointAt(line, 125)).toMatchObject({ x: 100 });
  expect(Math.abs(pointAt(line, 125).y - 25)).toBeLessThan(3);
  expect(pointAt(line, -10)).toEqual({ x: 0, y: 0 });
  expect(pointAt(line, 999)).toEqual({ x: 100, y: 50 });
});

test("un trazo vacío no rompe", () => {
  const line = sample(0, () => ({ x: 3, y: 4 }));
  expect(pointAt(line, 10)).toEqual({ x: 3, y: 4 });
});
