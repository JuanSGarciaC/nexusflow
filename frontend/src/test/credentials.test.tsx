import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { setAccessToken } from "../api/session";
import { ADMIN_ME, VIEWER_ME, installFakeApi, json, type Handler } from "./fakeApi";
import { renderApp } from "./render";

const SECRET = "sk_live_no_debe_verse_nunca_123456";

const STORED = {
  id: "33333333-3333-4333-8333-333333333333",
  name: "erp_token",
  type: "bearer_token",
  allowed_hosts: ["api.erp.example"],
  hint: "…3456",
  key_fingerprint: "k1",
  description: null,
  created_by: null,
  created_at: "2026-10-28T10:00:00Z",
  updated_at: "2026-10-28T10:00:00Z",
  secret_updated_at: null,
  last_used_at: null,
};

function session(me: typeof ADMIN_ME, routes: Record<string, Handler>) {
  return installFakeApi({
    "POST /api/v1/auth/refresh": () => json({ access_token: "tok", token_type: "bearer", expires_in: 900 }),
    "GET /api/v1/users/me": () => json(me),
    ...routes,
  });
}

// Pegar en lugar de teclear: igual que se introduce un secreto real, y sin el coste de
// simular cada pulsación en jsdom.
async function fill(user: ReturnType<typeof userEvent.setup>, field: HTMLElement, text: string) {
  await user.click(field);
  await user.paste(text);
}

beforeEach(() => setAccessToken(null));

describe("credenciales", () => {
  it("el secreto se envía al guardar y después no aparece en ninguna parte de la página", async () => {
    const stored: (typeof STORED)[] = [];
    let sent: unknown = null;
    session(ADMIN_ME, {
      "GET /api/v1/credentials": () => json(stored),
      "POST /api/v1/credentials": async (request) => {
        sent = await request.json();
        stored.push(STORED);
        return json(STORED, 201);
      },
    });
    renderApp("/credentials");
    const user = userEvent.setup();
    expect(await screen.findByText("Aún no hay credenciales.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Nueva credencial" }));
    const dialog = await screen.findByRole("dialog");
    const save = within(dialog).getByRole("button", { name: "Guardar" });
    await fill(user, within(dialog).getByLabelText(/Nombre/), "erp_token");
    await fill(user, within(dialog).getByLabelText(/^Token/), SECRET);
    expect(save).toBeDisabled(); // sin hosts permitidos no se puede guardar
    expect(within(dialog).getByLabelText(/^Token/)).toHaveAttribute("type", "password");
    await user.type(within(dialog).getByLabelText(/Hosts permitidos/), "api.erp.example{Enter}");
    expect(save).toBeEnabled();
    await user.click(save);

    expect(await screen.findByText("erp_token")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(sent).toEqual({
      name: "erp_token",
      type: "bearer_token",
      allowed_hosts: ["api.erp.example"],
      secret: { token: SECRET },
    });
    expect(screen.getByText("…3456")).toBeInTheDocument(); // solo la pista del servidor
    expect(document.body.innerHTML).not.toContain(SECRET);

    // Al reabrir el formulario no queda nada del secreto anterior.
    await user.click(screen.getByRole("button", { name: "Nueva credencial" }));
    expect(within(await screen.findByRole("dialog")).getByLabelText(/^Token/)).toHaveValue("");
  });

  it("cambiar de tipo descarta el secreto ya escrito: no viaja con el tipo nuevo", async () => {
    let sent: { secret?: Record<string, string> } | null = null;
    session(ADMIN_ME, {
      "GET /api/v1/credentials": () => json([]),
      "POST /api/v1/credentials": async (request) => {
        sent = (await request.json()) as typeof sent;
        return json({ ...STORED, type: "api_key_header" }, 201);
      },
    });
    renderApp("/credentials");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Nueva credencial" }));
    const dialog = await screen.findByRole("dialog");
    await fill(user, within(dialog).getByLabelText(/Nombre/), "erp_key");
    await fill(user, within(dialog).getByLabelText(/^Token/), SECRET);

    await user.click(within(dialog).getByLabelText("Tipo"));
    // En jsdom la lista desplegable de Mantine no cuenta como accesible: se busca con hidden.
    await user.click(await screen.findByRole("option", { name: "API key en cabecera", hidden: true }));
    await fill(user, within(dialog).getByLabelText(/^Cabecera/), "X-Api-Key");
    await fill(user, within(dialog).getByLabelText(/^Valor/), "otro-valor");
    await user.type(within(dialog).getByLabelText(/Hosts permitidos/), "api.erp.example{Enter}");
    await user.click(within(dialog).getByRole("button", { name: "Guardar" }));

    await waitFor(() => expect(sent).not.toBeNull());
    expect(sent!.secret).toEqual({ header_name: "X-Api-Key", value: "otro-valor" });
  });

  it("un error del servidor se muestra en el formulario y el secreto no sale de él", async () => {
    session(ADMIN_ME, {
      "GET /api/v1/credentials": () => json([]),
      "POST /api/v1/credentials": () =>
        json({ title: "Conflicto", status: 409, detail: "Ya existe una credencial con ese nombre.", request_id: "r9" }, 409),
    });
    renderApp("/credentials");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Nueva credencial" }));
    const dialog = await screen.findByRole("dialog");
    await fill(user, within(dialog).getByLabelText(/Nombre/), "erp_token");
    await fill(user, within(dialog).getByLabelText(/^Token/), SECRET);
    await user.type(within(dialog).getByLabelText(/Hosts permitidos/), "api.erp.example{Enter}");
    await user.click(within(dialog).getByRole("button", { name: "Guardar" }));

    expect(await within(dialog).findByText("Ya existe una credencial con ese nombre.")).toBeInTheDocument();
    // El campo sigue siendo de contraseña y el secreto no aparece como texto visible.
    expect(within(dialog).getByLabelText(/^Token/)).toHaveAttribute("type", "password");
    expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
  });

  it("un viewer no ve la página ni el botón de crear", async () => {
    session(VIEWER_ME, { "GET /api/v1/credentials": () => json({ title: "Prohibido", status: 403 }, 403) });
    renderApp("/credentials");
    expect(await screen.findByText("Sin acceso")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nueva credencial" })).not.toBeInTheDocument();
  });
});
