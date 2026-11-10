import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { setAccessToken } from "../api/session";
import { ADMIN_ME, VIEWER_ME, installFakeApi, json, type Handler } from "./fakeApi";
import { renderApp } from "./render";

const WF_ID = "33333333-3333-4333-8333-333333333333";
const TRIGGER_ID = "88888888-8888-4888-8888-888888888888";
const SUB_ID = "99999999-9999-4999-8999-999999999999";
const DELIVERY_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const SECRET = "whsec_solo_una_vez_0123456789abcdef";

const workflow = {
  id: WF_ID,
  name: "Cobros",
  description: null,
  status: "active",
  created_at: "2026-10-28T09:00:00Z",
  updated_at: "2026-10-28T09:00:00Z",
  published_version: null,
  draft_version: 1,
  created_by: null,
  draft: null,
  published: null,
};

const trigger = {
  id: TRIGGER_ID,
  workflow_id: WF_ID,
  name: "Pedidos ERP",
  is_active: true,
  rate_limit_per_minute: 120,
  url_path: `/api/v1/hooks/${TRIGGER_ID}`,
  last_received_at: null,
  created_at: "2026-10-28T09:00:00Z",
};

const subscription = {
  id: SUB_ID,
  workflow_id: null,
  name: "Avisos a facturación",
  url: "https://facturacion.example.com/hooks",
  events: ["run.failed"],
  is_active: true,
  created_at: "2026-10-28T09:00:00Z",
  updated_at: "2026-10-28T09:00:00Z",
};

const delivery = (status: string, id = DELIVERY_ID) => ({
  id,
  subscription_id: SUB_ID,
  event_id: "evt_1",
  event_type: "run.failed",
  status,
  attempt_count: 5,
  max_attempts: 5,
  last_status_code: 503,
  last_error: "Service Unavailable",
  next_attempt_at: null,
  delivered_at: null,
  payload: {},
  created_at: "2026-10-28T09:00:00Z",
});

function loggedInAs(me: typeof ADMIN_ME, extra: Record<string, Handler> = {}) {
  return installFakeApi({
    "POST /api/v1/auth/refresh": () => json({ access_token: "tok", token_type: "bearer", expires_in: 900 }),
    "GET /api/v1/users/me": () => json(me),
    [`GET /api/v1/workflows/${WF_ID}`]: () => json(workflow),
    [`GET /api/v1/workflows/${WF_ID}/triggers`]: () => json([trigger]),
    "GET /api/v1/webhook-subscriptions": () => json([subscription]),
    [`GET /api/v1/webhook-subscriptions/${SUB_ID}/deliveries`]: () =>
      json({ items: [delivery("dead"), delivery("delivered", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")], total: 45, limit: 20, offset: 0 }),
    ...extra,
  });
}

/** Todo lo que react-query retiene en memoria, consultas y mutaciones, como texto. */
function cached(queryClient: ReturnType<typeof renderApp>["queryClient"]): string {
  return JSON.stringify([
    queryClient.getQueryCache().getAll().map((q) => q.state.data),
    queryClient.getMutationCache().getAll().map((m) => m.state.data),
  ]);
}

beforeEach(() => setAccessToken(null));

describe("disparadores de un flujo", () => {
  it("un viewer no ve la pestaña", async () => {
    loggedInAs(VIEWER_ME);
    renderApp(`/workflows/${WF_ID}`);
    await screen.findByRole("tab", { name: "Definición" });
    expect(screen.queryByRole("tab", { name: "Disparadores" })).not.toBeInTheDocument();
  });

  it("lista nombre, URL completa y límite", async () => {
    loggedInAs(ADMIN_ME);
    renderApp(`/workflows/${WF_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Disparadores" }));
    expect(await screen.findByText("Pedidos ERP")).toBeInTheDocument();
    expect(screen.getByText(`${window.location.origin}/api/v1/hooks/${TRIGGER_ID}`)).toBeInTheDocument();
    expect(screen.getByText("120/min")).toBeInTheDocument();
  });

  it("crear muestra el secreto una sola vez y no lo deja en la caché", async () => {
    let body: unknown;
    loggedInAs(ADMIN_ME, {
      [`POST /api/v1/workflows/${WF_ID}/triggers`]: async (request) => {
        body = await request.json();
        return json({ ...trigger, secret: SECRET }, 201);
      },
    });
    const { queryClient } = renderApp(`/workflows/${WF_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Disparadores" }));
    await user.click(await screen.findByRole("button", { name: "Nuevo disparador" }));
    await user.type(await screen.findByLabelText(/Nombre/), "Pedidos ERP");
    await user.click(screen.getByRole("button", { name: "Crear" }));

    const dialog = await screen.findByRole("dialog", { name: /Secreto/ });
    expect(dialog).toHaveTextContent("Copia este secreto ahora. No se volverá a mostrar.");
    expect(dialog).toHaveTextContent(SECRET);
    expect(body).toEqual({ name: "Pedidos ERP", rate_limit_per_minute: 60 });

    await user.click(within(dialog).getByRole("button", { name: "Ya lo copié" }));
    await waitFor(() => expect(screen.queryByText(SECRET)).not.toBeInTheDocument());
    expect(cached(queryClient)).not.toContain(SECRET);
  });

  it("rotar pide confirmación y muestra el secreto nuevo", async () => {
    const api = loggedInAs(ADMIN_ME, {
      [`POST /api/v1/triggers/${TRIGGER_ID}/rotate-secret`]: () => json({ ...trigger, secret: SECRET }),
    });
    renderApp(`/workflows/${WF_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Disparadores" }));
    await user.click(await screen.findByRole("button", { name: "Rotar secreto de Pedidos ERP" }));
    expect(api.calls.some((c) => c.path.endsWith("/rotate-secret"))).toBe(false);
    await user.click(await screen.findByRole("button", { name: "Rotar" }));
    expect(await screen.findByText(SECRET)).toBeInTheDocument();
  });

  it("pausar envía is_active false", async () => {
    let body: unknown;
    loggedInAs(ADMIN_ME, {
      [`PATCH /api/v1/triggers/${TRIGGER_ID}`]: async (request) => {
        body = await request.json();
        return json({ ...trigger, is_active: false });
      },
    });
    renderApp(`/workflows/${WF_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Disparadores" }));
    await user.click(await screen.findByRole("switch", { name: "Activar Pedidos ERP" }));
    await waitFor(() => expect(body).toEqual({ is_active: false }));
  });
});

describe("webhooks salientes", () => {
  it("un viewer que fuerza la URL ve el aviso de acceso y no tiene entrada en el menú", async () => {
    loggedInAs(VIEWER_ME);
    renderApp("/webhooks");
    expect(await screen.findByText("Sin acceso")).toBeInTheDocument();
    expect(screen.getByText("webhooks:manage")).toBeInTheDocument();
    expect(within(screen.getByRole("navigation")).queryByText("Webhooks")).not.toBeInTheDocument();
  });

  it("crear envía los datos y muestra el secreto una sola vez", async () => {
    let body: unknown;
    loggedInAs(ADMIN_ME, {
      "POST /api/v1/webhook-subscriptions": async (request) => {
        body = await request.json();
        return json({ ...subscription, secret: SECRET }, 201);
      },
    });
    const { queryClient } = renderApp("/webhooks");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Nueva suscripción" }));
    await user.type(await screen.findByLabelText(/Nombre/), "Avisos a facturación");
    await user.type(screen.getByLabelText(/URL de destino/), "https://facturacion.example.com/hooks");
    await user.click(screen.getByRole("button", { name: "Crear" }));

    const dialog = await screen.findByRole("dialog", { name: /Secreto/ });
    expect(dialog).toHaveTextContent(SECRET);
    expect(body).toEqual({
      name: "Avisos a facturación",
      url: "https://facturacion.example.com/hooks",
      events: ["run.succeeded", "run.failed"],
      workflow_id: null,
    });
    await user.click(within(dialog).getByRole("button", { name: "Ya lo copié" }));
    await waitFor(() => expect(screen.queryByText(SECRET)).not.toBeInTheDocument());
    expect(cached(queryClient)).not.toContain(SECRET);
  });

  it("las entregas muestran estado, intentos y error, y solo se reenvían las agotadas", async () => {
    const api = loggedInAs(ADMIN_ME, {
      [`POST /api/v1/webhook-deliveries/${DELIVERY_ID}/redeliver`]: () => json(delivery("pending")),
    });
    renderApp("/webhooks");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Entregas de Avisos a facturación" }));
    const drawer = await screen.findByRole("dialog");
    expect(await within(drawer).findByText("Agotada")).toBeInTheDocument();
    expect(within(drawer).getAllByText("5 de 5")).toHaveLength(2);
    expect(within(drawer).getAllByText(/Service Unavailable/)).toHaveLength(2);

    const buttons = within(drawer).getAllByRole("button", { name: "Reenviar" });
    expect(buttons[0]).toBeEnabled();
    expect(buttons[1]).toBeDisabled();
    await user.click(buttons[0] as HTMLElement);
    await waitFor(() => expect(api.calls.some((c) => c.path === `/api/v1/webhook-deliveries/${DELIVERY_ID}/redeliver`)).toBe(true));
  });

  it("las entregas se paginan con limit y offset", async () => {
    const urls: string[] = [];
    loggedInAs(ADMIN_ME, {
      [`GET /api/v1/webhook-subscriptions/${SUB_ID}/deliveries`]: (request) => {
        urls.push(new URL(request.url).search);
        return json({ items: [delivery("dead")], total: 45, limit: 20, offset: 0 });
      },
    });
    renderApp("/webhooks");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Entregas de Avisos a facturación" }));
    const drawer = await screen.findByRole("dialog");
    await user.click(await within(drawer).findByRole("button", { name: "2" }));
    await waitFor(() => expect(urls.some((u) => u.includes("offset=20") && u.includes("limit=20"))).toBe(true));
  });

  it("ping avisa del resultado, y del error si la API lo rechaza", async () => {
    loggedInAs(ADMIN_ME, {
      [`POST /api/v1/webhook-subscriptions/${SUB_ID}/ping`]: () =>
        json({ title: "Conflicto", status: 409, detail: "La suscripción está pausada.", request_id: "r3" }, 409),
    });
    renderApp("/webhooks");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Enviar ping a Avisos a facturación" }));
    expect(await screen.findByText("La suscripción está pausada.")).toBeInTheDocument();
  });
});
