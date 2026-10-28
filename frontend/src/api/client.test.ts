import { describe, expect, it } from "vitest";

import { ApiError, describeError, isConflict, unwrap } from "./client";

describe("errores RFC 9457", () => {
  it("unwrap convierte un Problem Details en ApiError con sus campos", async () => {
    const problem = {
      type: "https://nexusflow.dev/problems/validation-error",
      title: "Los datos enviados no son válidos",
      status: 422,
      request_id: "abc123",
      errors: [{ loc: ["body", "name"], msg: "Campo obligatorio", type: "missing" }],
    };
    const failing = Promise.resolve({ error: problem, response: new Response(null, { status: 422 }) });
    const error = await unwrap(failing).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(422);
    expect(apiError.requestId).toBe("abc123");
    expect(describeError(apiError)).toBe("Los datos enviados no son válidos · name: Campo obligatorio");
  });

  it("unwrap devuelve los datos si la respuesta es correcta", async () => {
    const ok = Promise.resolve({ data: { id: 1 }, response: new Response(null, { status: 200 }) });
    await expect(unwrap(ok)).resolves.toEqual({ id: 1 });
  });

  it("detecta conflictos de concurrencia optimista", () => {
    expect(isConflict(new ApiError(409, { title: "Conflict" }))).toBe(true);
    expect(isConflict(new ApiError(400, { title: "Bad" }))).toBe(false);
    expect(isConflict(new Error("x"))).toBe(false);
  });
});
