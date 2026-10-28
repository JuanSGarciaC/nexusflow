import createClient from "openapi-fetch";

import type { components, paths } from "./schema";
import { createAuthFetch } from "./session";

export type Schemas = components["schemas"];

/** Cliente tipado: rutas, parámetros y cuerpos se comprueban contra el OpenAPI del backend. */
export const api = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: createAuthFetch(),
  credentials: "same-origin",
});

/** Error de la API en formato RFC 9457 (Problem Details). */
export class ApiError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string | undefined;
  readonly requestId: string | undefined;
  readonly fieldErrors: { loc: (string | number)[]; msg: string }[];
  readonly body: Record<string, unknown>;

  constructor(status: number, body: Record<string, unknown>) {
    const title = typeof body.title === "string" ? body.title : `Error ${status}`;
    const detail = typeof body.detail === "string" ? body.detail : undefined;
    super(detail ?? title);
    this.name = "ApiError";
    this.status = status;
    this.title = title;
    this.detail = detail;
    this.requestId = typeof body.request_id === "string" ? body.request_id : undefined;
    this.fieldErrors = Array.isArray(body.errors)
      ? (body.errors as { loc: (string | number)[]; msg: string }[])
      : [];
    this.body = body;
  }
}

type Result<T> = { data?: T; error?: unknown; response: Response };

/** Devuelve `data` o lanza ApiError con el Problem Details del servidor. */
export async function unwrap<T>(promise: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await promise;
  if (response.ok) return data as T;
  const body =
    error && typeof error === "object"
      ? (error as Record<string, unknown>)
      : { title: response.statusText || "Error de red" };
  throw new ApiError(response.status, body);
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    const fields = error.fieldErrors.map((e) => `${e.loc.slice(1).join(".")}: ${e.msg}`);
    return [error.detail ?? error.title, ...fields].join(" · ");
  }
  if (error instanceof Error) return error.message;
  return "Error inesperado";
}

export function isConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}
