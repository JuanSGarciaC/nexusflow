/**
 * Sesión del navegador.
 *
 * - El access token (JWT, 15 min) vive SOLO en memoria: nunca en localStorage ni en
 *   sessionStorage, donde cualquier XSS podría leerlo.
 * - La sesión larga es la cookie HttpOnly del refresh token, que JavaScript no puede leer.
 *   Al recargar la página se recupera la sesión llamando a /auth/refresh.
 * - Las renovaciones se SERIALIZAN: si varias peticiones reciben 401 a la vez, todas esperan
 *   la misma renovación. Dos renovaciones simultáneas con el mismo refresh token se
 *   interpretarían en el servidor como robo (reutilización) y cerrarían la sesión (ADR-0005).
 */

type Listener = (token: string | null) => void;

let accessToken: string | null = null;
let inflight: Promise<string | null> | null = null;
const listeners = new Set<Listener>();

export const REFRESH_URL = "/api/v1/auth/refresh";

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
  for (const listener of listeners) listener(token);
}

export function onTokenChange(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Renueva el access token. Concurrencia segura: una sola petición en vuelo. */
export function refreshAccessToken(fetchImpl: typeof fetch = globalThis.fetch): Promise<string | null> {
  if (!inflight) {
    inflight = (async () => {
      try {
        const response = await fetchImpl(REFRESH_URL, {
          method: "POST",
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        if (!response.ok) {
          setAccessToken(null);
          return null;
        }
        const body = (await response.json()) as { access_token: string };
        setAccessToken(body.access_token);
        return body.access_token;
      } catch {
        setAccessToken(null);
        return null;
      } finally {
        inflight = null;
      }
    })();
  }
  return inflight;
}

const NO_RETRY_PREFIX = "/api/v1/auth/";

/**
 * `fetch` autenticado para el cliente OpenAPI: añade el Bearer y, ante un 401, renueva la
 * sesión una vez y repite la petición. Las rutas de /auth/ no se reintentan (un 401 en el
 * login es "credenciales incorrectas", no "token caducado").
 */
export function createAuthFetch(fetchImpl?: typeof fetch) {
  return async function authFetch(input: Request): Promise<Response> {
    // Se resuelve en cada llamada (no al importar): respeta un fetch sustituido después.
    const doFetch = fetchImpl ?? globalThis.fetch;
    const retryCopy = input.clone();
    const first = await doFetch(withToken(input, accessToken));
    const path = new URL(input.url, window.location.origin).pathname;
    if (first.status !== 401 || path.startsWith(NO_RETRY_PREFIX)) return first;

    const renewed = await refreshAccessToken(doFetch);
    if (!renewed) return first;
    return doFetch(withToken(retryCopy, renewed));
  };
}

function withToken(request: Request, token: string | null): Request {
  if (!token) return request;
  const headers = new Headers(request.headers);
  headers.set("Authorization", `Bearer ${token}`);
  return new Request(request, { headers });
}
