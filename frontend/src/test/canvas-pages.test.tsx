import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { setAccessToken } from "../api/session";
import { ADMIN_ME, VIEWER_ME, installFakeApi, json, type Handler } from "./fakeApi";
import { renderApp } from "./render";

const WF_ID = "33333333-3333-4333-8333-333333333333";
const RUN_ID = "55555555-5555-4555-8555-555555555555";
const NEW_RUN_ID = "77777777-7777-4777-8777-777777777777";

const definition = (stepId: string) => ({
  schema_version: 1,
  inputs: [],
  start: stepId,
  steps: [{ id: stepId, type: "transform", config: { output: { ok: true } } }],
});

const version = (number: number, stepId: string, state = "published") => ({
  id: `44444444-4444-4444-8444-44444444444${number}`,
  version: number,
  state,
  revision: 1,
  definition_hash: "abcdef0123456789",
  created_at: "2026-10-28T09:00:00Z",
  updated_at: "2026-10-28T09:00:00Z",
  published_at: state === "published" ? "2026-10-28T09:00:00Z" : null,
  created_by: null,
  published_by: null,
  definition: definition(stepId),
  issues: [],
});

const workflow = {
  id: WF_ID,
  name: "Cobros",
  description: null,
  status: "active",
  created_at: "2026-10-28T09:00:00Z",
  updated_at: "2026-10-28T09:00:00Z",
  published_version: 1,
  draft_version: 2,
  created_by: null,
  draft: version(2, "nuevo", "draft"),
  published: version(1, "viejo"),
};

const run = (status: string) => ({
  id: RUN_ID,
  workflow_id: WF_ID,
  version_id: "44444444-4444-4444-8444-444444444441",
  version: 1,
  status,
  trigger_type: "manual",
  triggered_by: null,
  idempotency_key: "clave-original",
  created_at: "2026-10-28T09:00:00Z",
  started_at: "2026-10-28T09:00:00Z",
  finished_at: "2026-10-28T09:00:02Z",
  duration_ms: 2000,
  error: null,
  input: { amount: 5 },
  output: null,
  deadline_at: "2026-10-30T09:00:00Z",
  steps: [
    {
      id: "66666666-6666-4666-8666-666666666666",
      step_id: "viejo",
      step_type: "transform",
      sequence: 1,
      status: "failed",
      side_effect: "none",
      idempotency_key: `${RUN_ID}:viejo`,
      attempt_count: 3,
      max_attempts: 3,
      output: null,
      error: { class: "http_503", message: "Service Unavailable" },
      next_attempt_at: null,
      started_at: "2026-10-28T09:00:00Z",
      finished_at: "2026-10-28T09:00:02Z",
      attempts: [],
    },
  ],
});

function loggedInAs(me: typeof ADMIN_ME, extra: Record<string, Handler> = {}) {
  return installFakeApi({
    "POST /api/v1/auth/refresh": () => json({ access_token: "tok", token_type: "bearer", expires_in: 900 }),
    "GET /api/v1/users/me": () => json(me),
    [`GET /api/v1/workflows/${WF_ID}`]: () => json(workflow),
    [`GET /api/v1/workflows/${WF_ID}/versions`]: () => json([version(2, "nuevo", "draft"), version(1, "viejo")]),
    [`GET /api/v1/workflows/${WF_ID}/versions/1`]: () => json(version(1, "viejo")),
    ...extra,
  });
}

beforeEach(() => setAccessToken(null));

describe("lienzo en flujos", () => {
  it("la pestaña Definición muestra el lienzo del borrador y conserva el editor JSON", async () => {
    loggedInAs(ADMIN_ME);
    renderApp(`/workflows/${WF_ID}`);
    expect(await screen.findByRole("button", { name: "Paso nuevo" })).toBeInTheDocument();
    expect(screen.getByLabelText("Definición del flujo")).toBeInTheDocument();
  });

  it("el lienzo sigue al JSON mientras se edita y conserva el último válido", async () => {
    loggedInAs(ADMIN_ME);
    renderApp(`/workflows/${WF_ID}`);
    await screen.findByRole("button", { name: "Paso nuevo" });
    const user = userEvent.setup();
    const editor = screen.getByLabelText("Definición del flujo");
    await user.clear(editor);
    await user.click(editor);
    await user.paste(JSON.stringify(definition("editado")));
    expect(await screen.findByRole("button", { name: "Paso editado" })).toBeInTheDocument();
    await user.type(editor, "{{");
    expect(screen.getByRole("button", { name: "Paso editado" })).toBeInTheDocument();
  });

  it("Ver en una versión pide esa versión y la muestra en solo lectura", async () => {
    const api = loggedInAs(ADMIN_ME);
    renderApp(`/workflows/${WF_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Versiones" }));
    await user.click(await screen.findByRole("button", { name: "Abrir versión 1" }));
    expect(await screen.findByRole("button", { name: "Paso viejo" })).toBeInTheDocument();
    expect(api.calls.some((c) => c.path === `/api/v1/workflows/${WF_ID}/versions/1`)).toBe(true);
    expect(screen.getByRole("heading", { name: "Versión 1" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Guardar/ })).not.toBeInTheDocument();
  });

  it("la creación muestra la vista previa de la plantilla", async () => {
    loggedInAs(ADMIN_ME);
    renderApp("/workflows/new");
    expect(await screen.findByRole("button", { name: "Paso calc" })).toBeInTheDocument();
  });
});

describe("lienzo y reejecución en el detalle de ejecución", () => {
  it("usa la definición de la versión de la ejecución, no la actual", async () => {
    const api = loggedInAs(ADMIN_ME, { [`GET /api/v1/runs/${RUN_ID}`]: () => json(run("failed")) });
    renderApp(`/runs/${RUN_ID}`);
    expect(await screen.findByRole("button", { name: "Paso viejo: fallida, 3 de 3 intentos" })).toBeInTheDocument();
    expect(api.calls.some((c) => c.path === `/api/v1/workflows/${WF_ID}/versions/1`)).toBe(true);
    expect(api.calls.some((c) => c.path === `/api/v1/workflows/${WF_ID}`)).toBe(false);
  });

  it("si la versión no se puede cargar, la página sigue mostrando los pasos", async () => {
    loggedInAs(ADMIN_ME, {
      [`GET /api/v1/runs/${RUN_ID}`]: () => json(run("failed")),
      [`GET /api/v1/workflows/${WF_ID}/versions/1`]: () => json({ title: "Not Found", status: 404 }, 404),
    });
    renderApp(`/runs/${RUN_ID}`);
    expect(await screen.findByText("1. viejo")).toBeInTheDocument();
    expect(await screen.findByText("No se pudo cargar el lienzo de esta versión.")).toBeInTheDocument();
  });

  it("Reejecutar envía la misma entrada y versión, sin clave, y abre la ejecución nueva", async () => {
    let body: unknown;
    loggedInAs(ADMIN_ME, {
      [`GET /api/v1/runs/${RUN_ID}`]: () => json(run("failed")),
      [`GET /api/v1/runs/${NEW_RUN_ID}`]: () => json({ ...run("pending"), id: NEW_RUN_ID, steps: [] }),
      [`POST /api/v1/workflows/${WF_ID}/runs`]: async (request) => {
        body = await request.json();
        return json({ ...run("pending"), id: NEW_RUN_ID }, 202);
      },
    });
    const { router } = renderApp(`/runs/${RUN_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Reejecutar" }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/runs/${NEW_RUN_ID}`));
    expect(body).toEqual({ input: { amount: 5 }, version: 1 });
  });

  it("si la API rechaza la reejecución, muestra el error y no navega", async () => {
    loggedInAs(ADMIN_ME, {
      [`GET /api/v1/runs/${RUN_ID}`]: () => json(run("failed")),
      [`POST /api/v1/workflows/${WF_ID}/runs`]: () =>
        json({ title: "Conflicto", status: 409, detail: "El flujo está archivado.", request_id: "r9" }, 409),
    });
    const { router } = renderApp(`/runs/${RUN_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Reejecutar" }));
    expect(await screen.findByText("El flujo está archivado.")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(`/runs/${RUN_ID}`);
  });

  it("un paso abierto no se cierra solo cuando termina mientras se observa la ejecución", async () => {
    let status = "retry_scheduled";
    loggedInAs(ADMIN_ME, {
      [`GET /api/v1/runs/${RUN_ID}`]: () => {
        const r = run(status === "succeeded" ? "succeeded" : "running");
        return json({ ...r, steps: r.steps.map((s) => ({ ...s, status })) });
      },
    });
    const { queryClient } = renderApp(`/runs/${RUN_ID}`);
    const control = await screen.findByRole("button", { name: /1\. viejo/ });
    expect(control).toHaveAttribute("aria-expanded", "true");
    status = "succeeded";
    await queryClient.invalidateQueries({ queryKey: ["run", RUN_ID] });
    await screen.findByRole("button", { name: "Reejecutar" });
    expect(screen.getByRole("button", { name: /1\. viejo/ })).toHaveAttribute("aria-expanded", "true");
  });

  it("no ofrece Reejecutar mientras la ejecución sigue activa", async () => {
    loggedInAs(ADMIN_ME, { [`GET /api/v1/runs/${RUN_ID}`]: () => json(run("needs_review")) });
    renderApp(`/runs/${RUN_ID}`);
    await screen.findByText("1. viejo");
    expect(screen.queryByRole("button", { name: "Reejecutar" })).not.toBeInTheDocument();
  });

  it("un viewer no ve Reejecutar", async () => {
    loggedInAs(VIEWER_ME, { [`GET /api/v1/runs/${RUN_ID}`]: () => json(run("failed")) });
    renderApp(`/runs/${RUN_ID}`);
    await screen.findByText("1. viejo");
    expect(screen.queryByRole("button", { name: "Reejecutar" })).not.toBeInTheDocument();
  });
});
