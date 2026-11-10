import { expect, test } from "vitest";

import { connectedPath, layoutFlow, type FlowDefinition } from "./layout";

type AnyStep = FlowDefinition["steps"][number];

const t = (id: string, next?: string) =>
  ({ id, type: "transform", config: { output: { ok: true } }, next: next ?? null }) as unknown as AnyStep;
const cond = (id: string, on_true?: string, on_false?: string) =>
  ({
    id,
    type: "condition",
    config: { when: { field: "input.x", op: "eq", value: 1 } },
    on_true: on_true ?? null,
    on_false: on_false ?? null,
  }) as unknown as AnyStep;
const def = (start: string, steps: AnyStep[]) => ({ schema_version: 1, start, steps, inputs: [] }) as unknown as FlowDefinition;

test("cadena lineal: un nivel por paso", () => {
  const { nodes, edges } = layoutFlow(def("a", [t("a", "b"), t("b", "c"), t("c")]));
  expect(nodes.map((n) => [n.id, n.level])).toEqual([["a", 0], ["b", 1], ["c", 2]]);
  expect(edges.map((e) => e.id)).toEqual(["a->b:next", "b->c:next"]);
  expect(nodes[1]).toMatchObject({ x: 280, y: 0, reachable: true, type: "transform", label: "b" });
});

test("condición: dos ramas en el mismo nivel, con su tipo", () => {
  const { nodes, edges } = layoutFlow(def("c", [cond("c", "si", "no"), t("si"), t("no")]));
  expect(nodes.find((n) => n.id === "si")).toMatchObject({ level: 1, y: 0 });
  expect(nodes.find((n) => n.id === "no")).toMatchObject({ level: 1, y: 130 });
  expect(edges.map((e) => e.kind)).toEqual(["true", "false"]);
});

test("ramas que se reúnen: el nivel es el camino más largo", () => {
  const d = def("c", [cond("c", "x", "fin"), t("x", "fin"), t("fin")]);
  expect(layoutFlow(d).nodes.find((n) => n.id === "fin")?.level).toBe(2);
});

test("destino inexistente: se omite la conexión y no lanza", () => {
  const { nodes, edges } = layoutFlow(def("a", [t("a", "fantasma")]));
  expect(nodes).toHaveLength(1);
  expect(edges).toHaveLength(0);
});

test("ciclo en un borrador: termina y dibuja cada paso una vez", () => {
  const { nodes } = layoutFlow(def("a", [t("a", "b"), t("b", "a")]));
  expect(nodes.map((n) => n.id).sort()).toEqual(["a", "b"]);
  expect(nodes.map((n) => n.level).sort()).toEqual([0, 1]);
});

test("paso inalcanzable: aparece marcado al final", () => {
  const { nodes } = layoutFlow(def("a", [t("a"), t("huerfano")]));
  expect(nodes.find((n) => n.id === "huerfano")).toMatchObject({ reachable: false, level: 1 });
});

test("sin pasos o con start inexistente: no lanza", () => {
  expect(layoutFlow(def("a", []))).toEqual({ nodes: [], edges: [] });
  expect(layoutFlow(def("nada", [t("a")])).nodes[0]).toMatchObject({ reachable: false, level: 0 });
});

test("JSON a medio escribir (sin steps, pasos sin id): no lanza", () => {
  expect(layoutFlow({} as FlowDefinition)).toEqual({ nodes: [], edges: [] });
  expect(layoutFlow({ start: "a", steps: [null, { type: "transform" }, t("a")] } as unknown as FlowDefinition).nodes).toHaveLength(1);
});

test("el nombre del paso es la etiqueta cuando existe", () => {
  const step = { ...t("a"), name: "Calcular total" } as unknown as AnyStep;
  expect(layoutFlow(def("a", [step])).nodes[0]?.label).toBe("Calcular total");
});

test("connectedPath incluye ancestros y descendientes, no ramas hermanas", () => {
  const { edges } = layoutFlow(def("c", [cond("c", "si", "no"), t("si", "fin"), t("no"), t("fin")]));
  const path = connectedPath(edges, "si");
  expect([...path.nodes].sort()).toEqual(["c", "fin", "si"]);
  expect([...path.edges].sort()).toEqual(["c->si:true", "si->fin:next"]);
});

test("connectedPath termina aunque haya un ciclo", () => {
  const { edges } = layoutFlow(def("a", [t("a", "b"), t("b", "a")]));
  expect([...connectedPath(edges, "a").nodes].sort()).toEqual(["a", "b"]);
});
