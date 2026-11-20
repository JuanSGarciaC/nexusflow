import { expect, test } from "vitest";

import { fakeContext } from "../../../test/fakeCanvas";
import { PacketEngine, segmentsFor } from "./engine";
import { sample } from "./geometry";

const LINE = sample(320, (d) => ({ x: d, y: 0 }));
const VIEW = { x: 0, y: 0, zoom: 1, dpr: 2, width: 400, height: 200 };
const GRID = { gap: 22, size: 1.2 };

function run(engine: PacketEngine, ms: number) {
  for (let t = 0; t < ms; t += 16) engine.step(16);
}

test("un paquete recorre la conexión y deja un anillo al llegar", () => {
  const engine = new PacketEngine(() => LINE, { lite: false, grid: GRID });
  engine.spawn([{ edgeId: "e", at: 0, bounces: 0, arrival: "ok" }]);
  engine.step(16);
  expect(engine.packets).toHaveLength(1);
  expect(engine.visible).toBe(true);
  run(engine, 1100);
  expect(engine.packets).toHaveLength(0);
  expect(engine.rings).toHaveLength(1);
  run(engine, 800);
  expect(engine.busy).toBe(false);
  expect(engine.visible).toBe(false);
});

test("un fallo estalla en chispas; en versión ligera solo anillo", () => {
  const full = new PacketEngine(() => LINE, { lite: false, random: () => 0.5 });
  full.spawn([{ edgeId: "e", at: 0, bounces: 0, arrival: "fail" }]);
  run(full, 1100);
  expect(full.particles.length).toBeGreaterThan(0);

  const lite = new PacketEngine(() => LINE, { lite: true });
  lite.spawn([{ edgeId: "e", at: 0, bounces: 0, arrival: "fail" }]);
  run(lite, 1100);
  expect(lite.particles).toHaveLength(0);
  expect(lite.rings).toHaveLength(1);
});

test("los rebotes alargan el viaje y cambian de color", () => {
  expect(segmentsFor(320, 0)).toHaveLength(1);
  const bounced = segmentsFor(320, 2);
  expect(bounced).toHaveLength(5);
  expect(bounced[1]).toMatchObject({ from: 1, to: 0.6 });
  expect(bounced[1]?.tint).not.toBe(bounced[0]?.tint);

  const engine = new PacketEngine(() => LINE, { lite: false });
  engine.spawn([{ edgeId: "e", at: 0, bounces: 1, arrival: "ok" }]);
  run(engine, 1100);
  // Tras el primer tramo sigue viva: está rebotando.
  expect(engine.packets).toHaveLength(1);
  const head = engine.head(engine.packets[0]!);
  expect(head?.tint).toBe(bounced[1]?.tint);
});

test("espera a que la conexión exista en el DOM y descarta si no aparece", () => {
  let line: typeof LINE | null = null;
  const engine = new PacketEngine(() => line, { lite: false });
  engine.spawn([{ edgeId: "e", at: 0, bounces: 0, arrival: "ok" }]);
  run(engine, 500);
  expect(engine.packets).toHaveLength(1);
  line = LINE;
  run(engine, 100);
  expect(engine.head(engine.packets[0]!)).not.toBeNull();

  const never = new PacketEngine(() => null, { lite: false });
  never.spawn([{ edgeId: "e", at: 0, bounces: 0, arrival: "ok" }]);
  run(never, 3200);
  expect(never.packets).toHaveLength(0);
});

test("un paquete programado más tarde aún no tiene cabeza", () => {
  const engine = new PacketEngine(() => LINE, { lite: false });
  engine.spawn([{ edgeId: "e", at: 500, bounces: 0, arrival: "ok" }]);
  engine.step(16);
  expect(engine.head(engine.packets[0]!)).toBeNull();
});

test("las conexiones en curso emiten paquetes cada cierto tiempo", () => {
  const engine = new PacketEngine(() => LINE, { lite: false });
  engine.setStreams(["e"]);
  engine.step(16);
  expect(engine.packets).toHaveLength(1);
  // El primero (1 s de viaje) aún no llegó cuando sale el segundo (cada 0,9 s).
  run(engine, 940);
  expect(engine.packets).toHaveLength(2);
  expect(engine.busy).toBe(true);
  engine.setStreams([]);
  engine.clear();
  expect(engine.busy).toBe(false);
});

test("dibuja estela, halo, retícula, anillos y chispas", () => {
  const { ctx, calls } = fakeContext();
  const engine = new PacketEngine(() => LINE, { lite: false, grid: GRID, random: () => 0.3 });
  engine.setGlow({} as CanvasImageSource);
  engine.spawn([
    { edgeId: "a", at: 0, bounces: 0, arrival: "fail" },
    { edgeId: "b", at: 900, bounces: 0, arrival: "ok" },
  ]);
  run(engine, 1200);
  engine.draw(ctx, VIEW);
  expect(calls).toContain("drawImage");
  expect(calls).toContain("stroke");
  expect(calls.filter((c) => c === "fill").length).toBeGreaterThan(20);
  expect(calls[0]).toBe("setTransform");

  const lite = new PacketEngine(() => LINE, { lite: true, grid: GRID });
  lite.spawn([{ edgeId: "a", at: 0, bounces: 0, arrival: "ok" }]);
  run(lite, 300);
  const plain = fakeContext();
  lite.draw(plain.ctx, VIEW);
  expect(plain.calls.filter((c) => c === "fill")).toHaveLength(2);
});
