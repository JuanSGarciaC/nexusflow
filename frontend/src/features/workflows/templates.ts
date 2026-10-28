/** Plantillas de partida para el editor (mismo formato que docs/workflow-dsl.md). */
export const TEMPLATES: Record<string, { label: string; definition: unknown }> = {
  transform: {
    label: "Transformación simple",
    definition: {
      schema_version: 1,
      inputs: [{ name: "amount", type: "number" }],
      start: "calc",
      steps: [{ id: "calc", type: "transform", config: { output: { total: "{{ input.amount }}" } } }],
    },
  },
  http_condition: {
    label: "Consulta HTTP + condición",
    definition: {
      schema_version: 1,
      inputs: [{ name: "order_id", type: "string" }],
      start: "fetch_order",
      steps: [
        {
          id: "fetch_order",
          type: "http_request",
          config: { method: "GET", url: "https://api.example.com/orders/{{ input.order_id }}" },
          timeout_seconds: 10,
          retry: { max_attempts: 3, initial_backoff_seconds: 2 },
          next: "is_paid",
        },
        {
          id: "is_paid",
          type: "condition",
          config: { when: { field: "steps.fetch_order.output.body.status", op: "eq", value: "paid" } },
          on_true: "summary",
          on_false: null,
        },
        { id: "summary", type: "transform", config: { output: { order: "{{ steps.fetch_order.output.body.id }}" } } },
      ],
    },
  },
};

export function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

/** Parsea el JSON del editor. Devuelve el valor o un mensaje de error de sintaxis. */
export function parseJson(text: string): { ok: true; value: unknown } | { ok: false; message: string } {
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : "JSON no válido" };
  }
}
