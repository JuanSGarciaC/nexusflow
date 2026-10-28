import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createAuthFetch, getAccessToken, refreshAccessToken, setAccessToken } from "./session";

function response(status: number, body: unknown = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("sesión del navegador", () => {
  beforeEach(() => setAccessToken(null));
  afterEach(() => setAccessToken(null));

  it("serializa renovaciones concurrentes en una sola petición", async () => {
    let resolveRefresh: (r: Response) => void = () => {};
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => (resolveRefresh = resolve)));
    const pending = [1, 2, 3, 4, 5].map(() => refreshAccessToken(fetchMock as unknown as typeof fetch));
    resolveRefresh(response(200, { access_token: "nuevo" }));
    const tokens = await Promise.all(pending);
    // Cinco llamadas, una sola renovación: dos renovaciones con el mismo refresh token
    // se interpretarían en el servidor como robo y cerrarían la sesión.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(tokens).toEqual(["nuevo", "nuevo", "nuevo", "nuevo", "nuevo"]);
    expect(getAccessToken()).toBe("nuevo");
  });

  it("borra el token si la renovación falla", async () => {
    setAccessToken("viejo");
    const fetchMock = vi.fn(async () => response(401, { title: "No autenticado" }));
    expect(await refreshAccessToken(fetchMock as unknown as typeof fetch)).toBeNull();
    expect(getAccessToken()).toBeNull();
  });

  it("ante un 401 renueva una vez y repite la petición con el token nuevo", async () => {
    setAccessToken("caducado");
    const seen: (string | null)[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const req = input instanceof Request ? input : new Request(new URL(String(input), window.location.origin));
      if (req.url.endsWith("/api/v1/auth/refresh")) return response(200, { access_token: "fresco" });
      seen.push(req.headers.get("Authorization"));
      return req.headers.get("Authorization") === "Bearer fresco" ? response(200, { ok: true }) : response(401);
    });
    const authFetch = createAuthFetch(fetchMock as unknown as typeof fetch);
    const result = await authFetch(new Request(`${window.location.origin}/api/v1/runs`, { method: "POST", body: '{"a":1}' }));
    expect(result.status).toBe(200);
    expect(seen).toEqual(["Bearer caducado", "Bearer fresco"]);
  });

  it("no reintenta las rutas de autenticación (un 401 en login son credenciales malas)", async () => {
    const fetchMock = vi.fn(async () => response(401, { title: "No autenticado" }));
    const authFetch = createAuthFetch(fetchMock as unknown as typeof fetch);
    const result = await authFetch(new Request(`${window.location.origin}/api/v1/auth/login`, { method: "POST" }));
    expect(result.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("si la renovación falla devuelve el 401 original sin bucles", async () => {
    setAccessToken("caducado");
    const fetchMock = vi.fn(async () => response(401));
    const authFetch = createAuthFetch(fetchMock as unknown as typeof fetch);
    const result = await authFetch(new Request(`${window.location.origin}/api/v1/workflows`));
    expect(result.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(2); // petición original + un intento de renovación
  });
});
