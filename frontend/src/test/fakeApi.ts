/**
 * Backend falso para pruebas: rutas "MÉTODO /ruta" → respuesta JSON. Registra cada
 * llamada (con su cabecera Authorization) para poder comprobar el comportamiento.
 */
import { vi } from "vitest";

export type Handler = (request: Request) => Response | Promise<Response>;

export function json(body: unknown, status = 200): Response {
  const problem = status >= 400;
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": problem ? "application/problem+json" : "application/json" },
  });
}

export function installFakeApi(routes: Record<string, Handler>) {
  const calls: { method: string; path: string; auth: string | null }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(String(new URL(String(input), window.location.origin)), init);
    const url = new URL(request.url);
    calls.push({ method: request.method, path: url.pathname, auth: request.headers.get("Authorization") });
    const handler = routes[`${request.method} ${url.pathname}`];
    if (!handler) return json({ title: "Not Found", status: 404 }, 404);
    return handler(request);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

export const ADMIN_ME = {
  id: "00000000-0000-4000-8000-000000000001",
  email: "admin@example.com",
  role: "admin",
  is_active: true,
  created_at: "2026-10-28T10:00:00Z",
  last_login_at: null,
  permissions: [
    "audit:read", "credentials:manage", "metrics:read", "runs:read", "users:manage", "users:read",
    "webhooks:manage", "workflows:execute", "workflows:read", "workflows:write",
  ],
};

export const VIEWER_ME = {
  ...ADMIN_ME,
  id: "00000000-0000-4000-8000-000000000002",
  email: "viewer@example.com",
  role: "viewer",
  permissions: ["metrics:read", "runs:read", "workflows:read"],
};
