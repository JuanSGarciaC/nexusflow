import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { setAccessToken } from "../api/session";
import { ADMIN_ME, VIEWER_ME, installFakeApi, json } from "./fakeApi";
import { renderApp } from "./render";

function entry(id: number, action: string) {
  return {
    id,
    action,
    actor_email: "admin@example.com",
    actor_user_id: ADMIN_ME.id,
    details: { role: "operator" },
    ip_address: "203.0.113.7",
    occurred_at: "2026-10-28T10:00:00Z",
    request_id: `req-${id}`,
    resource_id: "44444444-4444-4444-8444-444444444444",
    resource_type: "user",
  };
}

function session(me: typeof ADMIN_ME, audit: (query: URLSearchParams) => Response) {
  const queries: URLSearchParams[] = [];
  installFakeApi({
    "POST /api/v1/auth/refresh": () => json({ access_token: "tok", token_type: "bearer", expires_in: 900 }),
    "GET /api/v1/users/me": () => json(me),
    "GET /api/v1/audit-logs": (request) => {
      const query = new URL(request.url).searchParams;
      queries.push(query);
      return audit(query);
    },
  });
  return queries;
}

beforeEach(() => setAccessToken(null));

describe("auditoría", () => {
  it("pagina por cursor: «Cargar más» pide before_id y añade sin repetir", async () => {
    const queries = session(ADMIN_ME, (q) =>
      q.get("before_id") === "50"
        ? json({ items: [entry(49, "auth.login")], next_before_id: null })
        : json({ items: [entry(51, "user.role_changed"), entry(50, "auth.logout")], next_before_id: 50 }),
    );
    renderApp("/audit");
    const user = userEvent.setup();
    expect(await screen.findByText("user.role_changed")).toBeInTheDocument();
    expect(screen.getAllByText("203.0.113.7")).toHaveLength(2);
    expect(screen.getAllByText('{"role":"operator"}')).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Cargar más" }));
    expect(await screen.findByText("auth.login")).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(1 + 3); // cabecera + 3 entradas, sin duplicados
    expect(queries.map((q) => q.get("before_id"))).toEqual([null, "50"]);
    expect(queries.every((q) => q.get("limit") === "50")).toBe(true);
    // Sin más páginas, el botón desaparece.
    await waitFor(() => expect(screen.queryByRole("button", { name: "Cargar más" })).not.toBeInTheDocument());
  });

  it("el filtro manda el prefijo de acción al servidor y vuelve a la primera página", async () => {
    const queries = session(ADMIN_ME, (q) =>
      q.get("action_prefix") === "auth."
        ? json({ items: [entry(10, "auth.login")], next_before_id: null })
        : json({ items: [entry(11, "user.role_changed")], next_before_id: 5 }),
    );
    renderApp("/audit");
    const user = userEvent.setup();
    expect(await screen.findByText("user.role_changed")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Filtrar por acción"), "auth.");
    expect(await screen.findByText("auth.login")).toBeInTheDocument();
    expect(screen.queryByText("user.role_changed")).not.toBeInTheDocument();
    const last = queries.at(-1)!;
    expect(last.get("action_prefix")).toBe("auth.");
    expect(last.get("before_id")).toBeNull();
    // Con espera (debounce): no se lanza una consulta por cada tecla.
    expect(queries.filter((q) => q.get("action_prefix") !== null).length).toBeLessThan("auth.".length);
  });

  it("un error del servidor se muestra con su referencia", async () => {
    session(ADMIN_ME, () =>
      json({ title: "Error interno", status: 500, detail: "Algo falló.", request_id: "req-err" }, 500),
    );
    renderApp("/audit");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("req-err");
  });

  it("sin audit:read no se muestra el registro", async () => {
    session(VIEWER_ME, () => json({ title: "Prohibido", status: 403 }, 403));
    renderApp("/audit");
    expect(await screen.findByText("Sin acceso")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
