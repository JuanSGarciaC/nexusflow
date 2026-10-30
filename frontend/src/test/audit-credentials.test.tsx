import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { setAccessToken } from "../api/session";
import { ADMIN_ME, VIEWER_ME, installFakeApi, json, type Handler } from "./fakeApi";
import { renderApp } from "./render";

function loggedInAs(me: typeof ADMIN_ME, extra: Record<string, Handler> = {}) {
  return installFakeApi({
    "POST /api/v1/auth/refresh": () => json({ access_token: "tok", token_type: "bearer", expires_in: 900 }),
    "GET /api/v1/users/me": () => json(me),
    ...extra,
  });
}

beforeEach(() => setAccessToken(null));

const entry = (id: number, over: Record<string, unknown> = {}) => ({
  id,
  occurred_at: "2026-10-28T09:00:00Z",
  action: "auth.login",
  actor_user_id: "00000000-0000-4000-8000-000000000001",
  actor_email: "admin@example.com",
  resource_type: "user",
  resource_id: "aaaaaaaa-1111-4111-8111-111111111111",
  ip_address: "203.0.113.7",
  request_id: "req-1",
  details: {},
  ...over,
});

describe("auditoría", () => {
  it("lista las acciones; sin actor humano (cron) muestra un guion y los detalles en JSON", async () => {
    loggedInAs(ADMIN_ME, {
      "GET /api/v1/audit-logs": () =>
        json({
          items: [
            entry(2, { action: "run.create", actor_email: null, actor_user_id: null, ip_address: null, details: { source: "cron" } }),
            entry(1),
          ],
          next_before_id: null,
        }),
    });
    renderApp("/audit");
    await screen.findByText("run.create"); // hasta que llegan los datos solo existe la fila de cabecera
    const rows = screen.getAllByRole("row");
    const cron = rows.find((r) => within(r).queryByText("run.create"));
    expect(cron).toBeDefined();
    // Actor e IP ausentes → "—" (dos guiones); los detalles se muestran tal cual.
    expect(within(cron as HTMLElement).getAllByText("—")).toHaveLength(2);
    expect(within(cron as HTMLElement).getByText('{"source":"cron"}')).toBeInTheDocument();
    const login = rows.find((r) => within(r).queryByText("auth.login")) as HTMLElement;
    expect(within(login).getByText("admin@example.com")).toBeInTheDocument();
    expect(within(login).getByText("203.0.113.7")).toBeInTheDocument();
    // El id del recurso se recorta a 8 caracteres.
    expect(within(login).getByText(/user aaaaaaaa$/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cargar más" })).not.toBeInTheDocument();
  });

  it("filtra por prefijo de acción en el servidor", async () => {
    const prefixes: (string | null)[] = [];
    loggedInAs(ADMIN_ME, {
      "GET /api/v1/audit-logs": (request) => {
        const prefix = new URL(request.url).searchParams.get("action_prefix");
        prefixes.push(prefix);
        return json({ items: [entry(1, { action: prefix ? `${prefix}login` : "run.create" })], next_before_id: null });
      },
    });
    renderApp("/audit");
    const user = userEvent.setup();
    await screen.findByText("run.create");
    await user.type(screen.getByLabelText("Filtrar por acción"), "auth.");
    expect(await screen.findByText("auth.login")).toBeInTheDocument();
    // Con el retardo (debounce) no se pide una consulta por cada tecla: solo la inicial y la final.
    expect(prefixes).toEqual([null, "auth."]);
  });

  it("carga más resultados con before_id y oculta el botón al llegar al final", async () => {
    const before: (string | null)[] = [];
    loggedInAs(ADMIN_ME, {
      "GET /api/v1/audit-logs": (request) => {
        const cursor = new URL(request.url).searchParams.get("before_id");
        before.push(cursor);
        return cursor
          ? json({ items: [entry(1, { action: "auth.logout" })], next_before_id: null })
          : json({ items: [entry(2, { action: "auth.login" })], next_before_id: 2 });
      },
    });
    renderApp("/audit");
    const user = userEvent.setup();
    await screen.findByText("auth.login");
    await user.click(screen.getByRole("button", { name: "Cargar más" }));
    expect(await screen.findByText("auth.logout")).toBeInTheDocument();
    // La página nueva se añade a la anterior, no la sustituye.
    expect(screen.getByText("auth.login")).toBeInTheDocument();
    expect(before).toEqual([null, "2"]);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Cargar más" })).not.toBeInTheDocument());
  });

  it("muestra el error de la API", async () => {
    loggedInAs(ADMIN_ME, {
      "GET /api/v1/audit-logs": () => json({ title: "Error interno", status: 500, request_id: "r-500" }, 500),
    });
    renderApp("/audit");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Error interno");
    expect(alert).toHaveTextContent("r-500");
  });

  it("un viewer ve un aviso en lugar de la página", async () => {
    loggedInAs(VIEWER_ME);
    renderApp("/audit");
    expect(await screen.findByText("Sin acceso")).toBeInTheDocument();
    expect(screen.getByText("audit:read")).toBeInTheDocument();
    expect(screen.queryByLabelText("Filtrar por acción")).not.toBeInTheDocument();
  });
});

// Teclear en varios campos de Mantine en jsdom tarda más que los 5 s por defecto de Vitest.
const SLOW_FORM = 20_000;

const credential = (over: Record<string, unknown> = {}) => ({
  id: "55555555-5555-4555-8555-555555555555",
  name: "demo_crm",
  type: "bearer_token",
  description: null,
  allowed_hosts: ["crm.example.com", "api.example.com"],
  hint: "…e65f",
  key_fingerprint: "abc123",
  created_by: null,
  created_at: "2026-10-28T09:00:00Z",
  updated_at: "2026-10-28T09:00:00Z",
  secret_updated_at: null,
  last_used_at: null,
  ...over,
});

describe("credenciales", () => {
  it("lista solo metadatos: hosts, pista y 'Último uso', nunca un secreto", async () => {
    loggedInAs(ADMIN_ME, { "GET /api/v1/credentials": () => json([credential()]) });
    renderApp("/credentials");
    expect(await screen.findByText("demo_crm")).toBeInTheDocument();
    expect(screen.getByText("bearer_token")).toBeInTheDocument();
    expect(screen.getByText("crm.example.com, api.example.com")).toBeInTheDocument();
    expect(screen.getByText("…e65f")).toBeInTheDocument();
    expect(screen.queryByText("Aún no hay credenciales.")).not.toBeInTheDocument();
  });

  it("sin credenciales lo indica", async () => {
    loggedInAs(ADMIN_ME, { "GET /api/v1/credentials": () => json([]) });
    renderApp("/credentials");
    expect(await screen.findByText("Aún no hay credenciales.")).toBeInTheDocument();
  });

  it("crea una credencial: exige nombre y hosts, envía el secreto una vez y cierra el formulario", async () => {
    let created = false;
    let body: Record<string, unknown> = {};
    loggedInAs(ADMIN_ME, {
      "GET /api/v1/credentials": () => json(created ? [credential({ name: "nueva" })] : []),
      "POST /api/v1/credentials": async (request) => {
        body = (await request.json()) as Record<string, unknown>;
        created = true;
        return json(credential({ name: "nueva" }), 201);
      },
    });
    renderApp("/credentials");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Nueva credencial" }));
    const dialog = await screen.findByRole("dialog");
    const save = within(dialog).getByRole("button", { name: "Guardar" });
    expect(save).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/Nombre/), "nueva");
    expect(save).toBeDisabled(); // falta al menos un host
    await user.type(within(dialog).getByLabelText(/Hosts permitidos/), "api.example.com{Enter}");
    await user.type(within(dialog).getByLabelText(/Token/), "valor-de-prueba");
    expect(save).toBeEnabled();
    await user.click(save);

    await waitFor(() =>
      expect(body).toEqual({
        name: "nueva",
        type: "bearer_token",
        allowed_hosts: ["api.example.com"],
        secret: { token: "valor-de-prueba" },
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // La lista se vuelve a pedir y el secreto no aparece en ninguna parte de la página.
    expect(await screen.findByText("nueva")).toBeInTheDocument();
    expect(screen.queryByText("valor-de-prueba")).not.toBeInTheDocument();
  });

  it("al cambiar de tipo descarta lo escrito y pide los campos del nuevo tipo", async () => {
    let body: Record<string, unknown> = {};
    loggedInAs(ADMIN_ME, {
      "GET /api/v1/credentials": () => json([]),
      "POST /api/v1/credentials": async (request) => {
        body = (await request.json()) as Record<string, unknown>;
        return json(credential(), 201);
      },
    });
    renderApp("/credentials");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Nueva credencial" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Token/), "secreto-anterior");
    await user.click(within(dialog).getByLabelText("Tipo"));
    await user.click(await screen.findByRole("option", { name: "Basic auth", hidden: true }));

    // El token tecleado no sobrevive al cambio y aparecen usuario y contraseña, vacíos.
    expect(within(dialog).queryByLabelText(/Token/)).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Usuario/)).toHaveValue("");
    expect(within(dialog).getByLabelText(/Contraseña/)).toHaveValue("");

    await user.type(within(dialog).getByLabelText(/Nombre/), "basica");
    await user.type(within(dialog).getByLabelText(/Hosts permitidos/), "api.example.com{Enter}");
    await user.type(within(dialog).getByLabelText(/Usuario/), "svc");
    await user.type(within(dialog).getByLabelText(/Contraseña/), "clave-de-prueba");
    await user.click(within(dialog).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(body).toMatchObject({ type: "basic_auth", secret: { username: "svc", password: "clave-de-prueba" } }));
    expect(JSON.stringify(body)).not.toContain("secreto-anterior");
  }, SLOW_FORM);

  it("si el servidor rechaza la credencial muestra el motivo y mantiene el formulario abierto", async () => {
    loggedInAs(ADMIN_ME, {
      "GET /api/v1/credentials": () => json([]),
      "POST /api/v1/credentials": () =>
        json({ title: "Conflicto", status: 409, detail: "Ya existe una credencial con ese nombre", request_id: "r-409" }, 409),
    });
    renderApp("/credentials");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Nueva credencial" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Nombre/), "demo_crm");
    await user.type(within(dialog).getByLabelText(/Hosts permitidos/), "api.example.com{Enter}");
    await user.type(within(dialog).getByLabelText(/Token/), "x");
    await user.click(within(dialog).getByRole("button", { name: "Guardar" }));
    // El aviso informativo del formulario también es un "alert": se localiza el de error por su texto.
    const alert = (await within(dialog).findByText("Ya existe una credencial con ese nombre")).closest('[role="alert"]');
    expect(alert).toHaveTextContent("r-409");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  }, SLOW_FORM);

  it("un viewer ve un aviso en lugar de la página", async () => {
    loggedInAs(VIEWER_ME);
    renderApp("/credentials");
    expect(await screen.findByText("Sin acceso")).toBeInTheDocument();
    expect(screen.getByText("credentials:manage")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nueva credencial" })).not.toBeInTheDocument();
  });
});
