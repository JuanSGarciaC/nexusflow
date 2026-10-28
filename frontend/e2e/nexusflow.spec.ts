import { expect, test } from "@playwright/test";

import { loginAs, nav } from "./helpers";

const CLASSIFIER = "Demo · Clasificar pedido";

test("operador ejecuta un flujo publicado y ve el resultado del worker", async ({ page }) => {
  await loginAs(page, "operator", "/workflows");
  await expect(page.getByRole("link", { name: "Nuevo flujo" })).toHaveCount(0); // no puede editar
  await page.getByRole("link", { name: CLASSIFIER }).click();
  await page.getByRole("tab", { name: "Ejecutar" }).click();
  await page.getByLabel("Entrada (JSON)").fill(JSON.stringify({ order_id: "E2E-1", amount: 5000, country: "CO" }));
  await page.getByRole("button", { name: "Ejecutar", exact: true }).click();
  await expect(page).toHaveURL(/\/runs\//);
  await expect(page.locator("[data-status=succeeded]").first()).toBeVisible({ timeout: 30_000 });
  // Importe ≥ 1000 → la condición envía el pedido a revisión.
  await expect(page.getByText('"decision": "revisión"').last()).toBeVisible();
});

test("la idempotencia evita ejecuciones duplicadas", async ({ page }) => {
  await loginAs(page, "operator", "/workflows");
  await page.getByRole("link", { name: CLASSIFIER }).click();
  const key = `e2e-${Date.now()}`;
  const runIds: string[] = [];
  for (let i = 0; i < 2; i++) {
    await page.getByRole("tab", { name: "Ejecutar" }).click();
    await page.getByLabel("Entrada (JSON)").fill(JSON.stringify({ order_id: "E2E-2", amount: 10, country: "ES" }));
    await page.getByLabel(/Clave de idempotencia/).fill(key);
    await page.getByRole("button", { name: "Ejecutar", exact: true }).click();
    await expect(page).toHaveURL(/\/runs\//);
    runIds.push(page.url().split("/runs/")[1] ?? "");
    await page.goBack();
  }
  expect(runIds[0]).toBeTruthy();
  expect(runIds[1]).toBe(runIds[0]);
});

test("editor crea un flujo: el validador detecta errores y luego lo publica", async ({ page }) => {
  await loginAs(page, "editor", "/workflows/new");
  const name = `E2E flujo ${Date.now()}`;
  await page.getByLabel("Nombre").fill(name);
  // Estructura válida, pero la transición apunta a un paso que no existe (error semántico).
  const broken = { start: "a", steps: [{ id: "a", type: "transform", config: { output: { x: 1 } }, next: "fantasma" }] };
  await page.getByLabel("Definición (JSON)").fill(JSON.stringify(broken));
  await page.getByRole("button", { name: "Validar" }).click();
  await expect(page.getByText("unknown_step")).toBeVisible();

  const fixed = { start: "a", steps: [{ id: "a", type: "transform", config: { output: { ok: true } } }] };
  await page.getByLabel("Definición (JSON)").fill(JSON.stringify(fixed));
  await page.getByRole("button", { name: "Validar" }).click();
  await expect(page.getByText("Definición válida")).toBeVisible();
  await page.getByRole("button", { name: "Crear borrador" }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await page.getByRole("button", { name: "Publicar v1" }).click();
  await expect(page.getByText("Versión 1 publicada")).toBeVisible();
  await page.getByRole("tab", { name: "Versiones" }).click();
  await expect(page.getByRole("cell", { name: "v1" })).toBeVisible();
});

test("viewer solo lee: sin ejecutar, sin editar y sin páginas de administración", async ({ page }) => {
  await loginAs(page, "viewer", "/");
  await expect(page.getByRole("heading", { name: "Panel" })).toBeVisible();
  await expect(nav(page).getByText("Usuarios")).toHaveCount(0);
  await expect(nav(page).getByText("Credenciales")).toHaveCount(0);
  await page.goto("/workflows");
  await page.getByRole("link", { name: CLASSIFIER }).click();
  await expect(page.getByRole("tab", { name: "Ejecutar" })).toHaveCount(0);
  await expect(page.getByLabel("Definición del flujo")).toHaveAttribute("readonly", "");
  // Aunque fuerce la URL, la API también lo impide: la página de usuarios no se muestra.
  await page.goto("/users");
  await expect(page.getByText("Sin acceso")).toBeVisible();
});

test("admin cambia un rol y el cambio queda en la auditoría", async ({ page }) => {
  await loginAs(page, "admin", "/users");
  const select = page.getByRole("combobox", { name: "Rol de demo-viewer@example.com" });
  await select.click();
  await page.getByRole("option", { name: "operator" }).click();
  await expect(select).toHaveValue("operator");
  // Se deja como estaba para que la suite sea repetible.
  await select.click();
  await page.getByRole("option", { name: "viewer" }).click();
  await expect(select).toHaveValue("viewer");

  await nav(page).getByText("Auditoría").click();
  await page.getByLabel("Filtrar por acción").fill("user.role_changed");
  await expect(page.getByText("user.role_changed").first()).toBeVisible();
});

test("la sesión sobrevive a una recarga y el logout la cierra en el servidor", async ({ page, context }) => {
  await loginAs(page, "viewer", "/workflows");
  await page.reload();
  await expect(page.getByRole("button", { name: "Salir" })).toBeVisible();
  const refresh = (await context.cookies()).find((c) => c.name === "nexusflow_refresh");
  expect(refresh?.httpOnly).toBe(true);
  expect(refresh?.sameSite).toBe("Strict");
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);

  await page.getByRole("button", { name: "Salir" }).click();
  await expect(page.getByRole("button", { name: "Entrar" })).toBeVisible();
  // Reutilizar la cookie antigua tras el logout no devuelve una sesión.
  const status = await page.evaluate(async () => (await fetch("/api/v1/auth/refresh", { method: "POST" })).status);
  expect(status).toBe(401);
});
