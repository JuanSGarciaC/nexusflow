import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { setAccessToken } from "../api/session";
import { ADMIN_ME, VIEWER_ME, installFakeApi, json, type Handler } from "./fakeApi";
import { renderApp } from "./render";

const EMPTY_METRICS = {
  window_start: "2026-10-27T10:00:00Z",
  window_end: "2026-10-28T10:00:00Z",
  bucket: "hour",
  workflow_id: null,
  runs_by_status: { succeeded: 3, failed: 2 },
  total_runs: 5,
  success_rate: 0.6,
  run_duration: { count: 5, avg_ms: 300, p50_ms: 250, p95_ms: 1200, max_ms: 1500 },
  attempts: 6,
  retries: 1,
  timeline: [{ start: "2026-10-28T09:00:00Z", total: 5, succeeded: 3, failed: 2 }],
  step_types: [{ step_type: "http_request", attempts: 3, failed_attempts: 3, p95_ms: 900 }],
  error_classes: [{ error_class: "http_404", count: 1 }],
  top_failing_workflows: [],
  recent_failures: [
    {
      run_id: "11111111-1111-4111-8111-111111111111",
      workflow_id: "22222222-2222-4222-8222-222222222222",
      workflow_name: "erp",
      status: "failed",
      error_class: "http_404",
      message: "Not Found",
      step_id: "call",
      finished_at: "2026-10-28T09:30:00Z",
    },
  ],
};

function loggedInAs(me: typeof ADMIN_ME, extra: Record<string, Handler> = {}) {
  return installFakeApi({
    "POST /api/v1/auth/refresh": () => json({ access_token: "tok", token_type: "bearer", expires_in: 900 }),
    "GET /api/v1/users/me": () => json(me),
    "GET /api/v1/metrics/summary": () => json(EMPTY_METRICS),
    ...extra,
  });
}

beforeEach(() => setAccessToken(null));

describe("autenticación", () => {
  it("sin sesión muestra el login y explica un error de credenciales", async () => {
    const api = installFakeApi({
      "POST /api/v1/auth/refresh": () => json({ title: "No autenticado", status: 401 }, 401),
      "POST /api/v1/auth/login": () =>
        json({ title: "No autenticado", status: 401, detail: "Email o contraseña incorrectos.", request_id: "r1" }, 401),
    });
    renderApp("/workflows");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/email/i), "ana@example.com");
    await user.type(screen.getByLabelText(/contraseña/i), "mala");
    await user.click(screen.getByRole("button", { name: "Entrar" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Email o contraseña incorrectos.");
    expect(alert).toHaveTextContent("r1");
    // El login fallido no dispara una renovación de sesión (no hay bucle 401 → refresh).
    expect(api.calls.filter((c) => c.path === "/api/v1/auth/refresh")).toHaveLength(1);
  });

  it("tras iniciar sesión vuelve a la página pedida", async () => {
    let loggedIn = false;
    installFakeApi({
      "POST /api/v1/auth/refresh": () => json({ title: "No autenticado", status: 401 }, 401),
      "POST /api/v1/auth/login": () => {
        loggedIn = true;
        return json({ access_token: "tok", token_type: "bearer", expires_in: 900 });
      },
      "GET /api/v1/users/me": () => (loggedIn ? json(ADMIN_ME) : json({ title: "No autenticado" }, 401)),
      "GET /api/v1/workflows": () => json({ items: [], total: 0, limit: 20, offset: 0 }),
    });
    const { router } = renderApp("/workflows");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/email/i), "admin@example.com");
    await user.type(screen.getByLabelText(/contraseña/i), "correcta-larga-123");
    await user.click(screen.getByRole("button", { name: "Entrar" }));
    await screen.findByRole("heading", { name: "Flujos" });
    expect(router.state.location.pathname).toBe("/workflows");
  });

  it("recupera la sesión con la cookie y envía el Bearer en cada petición", async () => {
    const api = loggedInAs(ADMIN_ME);
    renderApp("/");
    expect(await screen.findByTestId("stat-Tasa de éxito")).toHaveTextContent("60.0 %");
    const me = api.calls.find((c) => c.path === "/api/v1/users/me");
    expect(me?.auth).toBe("Bearer tok");
  });
});

describe("permisos en la interfaz", () => {
  it("un viewer no ve la gestión de usuarios ni de credenciales", async () => {
    loggedInAs(VIEWER_ME);
    renderApp("/");
    const nav = await screen.findByRole("navigation", { name: "Navegación principal" });
    await within(nav).findByText("Flujos");
    expect(within(nav).queryByText("Usuarios")).not.toBeInTheDocument();
    expect(within(nav).queryByText("Credenciales")).not.toBeInTheDocument();
    expect(within(nav).queryByText("Auditoría")).not.toBeInTheDocument();
  });

  it("si un viewer abre /users por URL ve un aviso, no la página", async () => {
    loggedInAs(VIEWER_ME);
    renderApp("/users");
    expect(await screen.findByText("Sin acceso")).toBeInTheDocument();
    expect(screen.getByText("users:read")).toBeInTheDocument();
  });
});

const WF_ID = "33333333-3333-4333-8333-333333333333";
const DEFINITION = { schema_version: 1, inputs: [], start: "a", steps: [{ id: "a", type: "transform", config: { output: {} } }] };
const version = (over: Record<string, unknown>) => ({
  id: "44444444-4444-4444-8444-444444444444",
  version: 2,
  state: "draft",
  revision: 3,
  definition_hash: "abcdef0123456789",
  created_at: "2026-10-28T09:00:00Z",
  updated_at: "2026-10-28T09:00:00Z",
  published_at: null,
  created_by: null,
  published_by: null,
  definition: DEFINITION,
  issues: [],
  ...over,
});
const workflow = (draft: unknown) => ({
  id: WF_ID,
  name: "Cobros",
  description: null,
  status: "active",
  created_at: "2026-10-28T09:00:00Z",
  updated_at: "2026-10-28T09:00:00Z",
  published_version: null,
  draft_version: 2,
  created_by: null,
  draft,
  published: null,
});

describe("editor de flujos", () => {
  it("no deja publicar un borrador con incidencias", async () => {
    loggedInAs(ADMIN_ME, {
      [`GET /api/v1/workflows/${WF_ID}`]: () =>
        json(workflow(version({ issues: [{ code: "cycle", message: "Hay un ciclo", location: "steps.a" }] }))),
    });
    renderApp(`/workflows/${WF_ID}`);
    expect(await screen.findByText(/1 incidencia/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publicar v2" })).toBeDisabled();
  });

  it("si otra persona guardó antes (409) avisa en lugar de sobrescribir", async () => {
    const api = loggedInAs(ADMIN_ME, {
      [`GET /api/v1/workflows/${WF_ID}`]: () => json(workflow(version({}))),
      [`PUT /api/v1/workflows/${WF_ID}/draft`]: () =>
        json({ title: "Conflicto", status: 409, detail: "El borrador cambió (revisión 4)" }, 409),
    });
    renderApp(`/workflows/${WF_ID}`);
    const editor = await screen.findByLabelText("Definición del flujo");
    const user = userEvent.setup();
    await user.clear(editor);
    await user.click(editor);
    const changed = { ...DEFINITION, steps: [{ id: "a", type: "transform", config: { output: { total: 1 } } }] };
    await user.paste(JSON.stringify(changed));
    expect(await screen.findByText("Cambios sin guardar")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Guardar borrador" }));
    expect(await screen.findByText("Otra persona guardó cambios")).toBeInTheDocument();
    // Se envió la revisión que se estaba editando: el servidor puede detectar el conflicto.
    expect(api.calls.some((c) => c.method === "PUT")).toBe(true);
  });
});

describe("programaciones", () => {
  const SCHEDULE_ID = "77777777-7777-4777-8777-777777777777";
  const schedule = (over: Record<string, unknown> = {}) => ({
    id: SCHEDULE_ID,
    workflow_id: WF_ID,
    name: "Cierre diario",
    cron_expression: "0 9 * * 1-5",
    timezone: "Europe/Madrid",
    input: {},
    is_active: true,
    next_run_at: "2026-10-29T08:00:00Z",
    last_run_at: null,
    last_run_id: null,
    last_error: "Falta la entrada obligatoria region",
    created_at: "2026-10-28T09:00:00Z",
    ...over,
  });
  const published = workflow(null);

  it("lista las programaciones, muestra el último error y permite pausar", async () => {
    const api = loggedInAs(ADMIN_ME, {
      [`GET /api/v1/workflows/${WF_ID}`]: () => json(published),
      [`GET /api/v1/workflows/${WF_ID}/schedules`]: () => json([schedule()]),
      [`PATCH /api/v1/schedules/${SCHEDULE_ID}`]: () => json(schedule({ is_active: false })),
    });
    renderApp(`/workflows/${WF_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Programaciones" }));
    expect(await screen.findByText("Cierre diario")).toBeInTheDocument();
    expect(screen.getByText("0 9 * * 1-5")).toBeInTheDocument();
    expect(screen.getByText("Falta la entrada obligatoria region")).toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: "Activar Cierre diario" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "PATCH")).toBe(true));
  });

  it("envía la expresión cron y la zona horaria al crear", async () => {
    let body: Record<string, unknown> = {};
    loggedInAs(ADMIN_ME, {
      [`GET /api/v1/workflows/${WF_ID}`]: () => json(published),
      [`GET /api/v1/workflows/${WF_ID}/schedules`]: () => json([]),
      [`POST /api/v1/workflows/${WF_ID}/schedules`]: async (r) => {
        body = (await r.json()) as Record<string, unknown>;
        return json(schedule(), 201);
      },
    });
    renderApp(`/workflows/${WF_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Programaciones" }));
    await user.click(await screen.findByRole("button", { name: "Nueva programación" }));
    await user.type(screen.getByLabelText(/Nombre/), "Informe");
    const cron = screen.getByLabelText(/Expresión cron/);
    await user.clear(cron);
    await user.type(cron, "*/15 * * * *");
    const zone = screen.getByLabelText(/Zona horaria/);
    await user.clear(zone);
    await user.type(zone, "Europe/Madrid");
    await user.click(screen.getByRole("button", { name: "Crear programación" }));
    await waitFor(() => expect(body).toMatchObject({ name: "Informe", cron_expression: "*/15 * * * *", timezone: "Europe/Madrid" }));
  });

  it("muestra el error del servidor si la expresión no es válida", async () => {
    loggedInAs(ADMIN_ME, {
      [`GET /api/v1/workflows/${WF_ID}`]: () => json(published),
      [`GET /api/v1/workflows/${WF_ID}/schedules`]: () => json([]),
      [`POST /api/v1/workflows/${WF_ID}/schedules`]: () =>
        json({ title: "Solicitud inválida", status: 422, detail: "Expresión cron no válida" }, 422),
    });
    renderApp(`/workflows/${WF_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Programaciones" }));
    await user.click(await screen.findByRole("button", { name: "Nueva programación" }));
    await user.type(screen.getByLabelText(/Nombre/), "x");
    await user.click(screen.getByRole("button", { name: "Crear programación" }));
    expect(await screen.findByText(/Expresión cron no válida/)).toBeInTheDocument();
  });

  it("un viewer no ve la pestaña de programaciones", async () => {
    loggedInAs(VIEWER_ME, { [`GET /api/v1/workflows/${WF_ID}`]: () => json(published) });
    renderApp(`/workflows/${WF_ID}`);
    await screen.findByRole("tab", { name: "Definición" });
    expect(screen.queryByRole("tab", { name: "Programaciones" })).not.toBeInTheDocument();
  });
});

describe("detalle de ejecución", () => {
  const RUN_ID = "55555555-5555-4555-8555-555555555555";
  const run = {
    id: RUN_ID,
    workflow_id: WF_ID,
    version_id: "44444444-4444-4444-8444-444444444444",
    version: 1,
    status: "needs_review",
    trigger_type: "manual",
    triggered_by: null,
    idempotency_key: null,
    created_at: "2026-10-28T09:00:00Z",
    started_at: "2026-10-28T09:00:00Z",
    finished_at: null,
    duration_ms: null,
    error: null,
    input: {},
    output: null,
    deadline_at: "2026-10-30T09:00:00Z",
    steps: [
      {
        id: "66666666-6666-4666-8666-666666666666",
        step_id: "charge",
        step_type: "http_request",
        sequence: 1,
        status: "needs_review",
        side_effect: "non_idempotent",
        idempotency_key: `${RUN_ID}:charge`,
        attempt_count: 1,
        max_attempts: 3,
        output: null,
        error: { class: "timeout_after_send", message: "Sin respuesta tras enviar" },
        next_attempt_at: null,
        started_at: "2026-10-28T09:00:00Z",
        finished_at: null,
        attempts: [],
      },
    ],
  };

  it("muestra la acción de resolver solo a quien puede ejecutar", async () => {
    loggedInAs(ADMIN_ME, { [`GET /api/v1/runs/${RUN_ID}`]: () => json(run) });
    renderApp(`/runs/${RUN_ID}`);
    expect(await screen.findByText("timeout_after_send")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Resolver" }));
    // El modal explica el riesgo y muestra la clave de idempotencia que se envió.
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("podría repetir el efecto");
    expect(dialog).toHaveTextContent(`${RUN_ID}:charge`);
  });

  it("un viewer ve el error pero no puede resolver ni cancelar", async () => {
    loggedInAs(VIEWER_ME, { [`GET /api/v1/runs/${RUN_ID}`]: () => json(run) });
    renderApp(`/runs/${RUN_ID}`);
    expect(await screen.findByText("timeout_after_send")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Resolver" })).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Cancelar" })).not.toBeInTheDocument();
  });
});
