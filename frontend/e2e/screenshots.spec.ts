/**
 * Genera las capturas de docs/screenshots a partir del stack real con datos de demostración.
 * No forma parte de la suite normal: solo corre con CAPTURE_SCREENSHOTS=1.
 *
 *   CAPTURE_SCREENSHOTS=1 E2E_BASE_URL=... E2E_PASSWORD=... pnpm exec playwright test screenshots
 *
 * Con E2E_WEBHOOK_SECRET (el MOCK_PARTNER_WEBHOOK_SECRET del .env) la suscripción de ejemplo
 * firma con el secreto que la API simulada verifica y la entrega del ping sale completada;
 * sin él, la API simulada responde 401 y la captura muestra un reintento programado.
 */
import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { PASSWORD, loginAs } from "./helpers";

test.skip(!process.env.CAPTURE_SCREENSHOTS, "Solo para regenerar las capturas del README");
test.use({ viewport: { width: 1360, height: 820 }, colorScheme: "dark" });

const OUT = fileURLToPath(new URL("../../docs/screenshots", import.meta.url));
const shot = (name: string) => ({ path: path.join(OUT, `${name}.png`), animations: "disabled" as const });

test("capturas del panel", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("button", { name: "Entrar" })).toBeVisible();
  await page.screenshot(shot("12-login"));

  await loginAs(page, "admin", "/");
  await expect(page.getByText("Tasa de éxito").first()).toBeVisible();
  await page.mouse.move(0, 0); // sin tooltips del gráfico
  await page.waitForTimeout(1200); // gráfico y tablas
  await page.screenshot(shot("01-panel"));

  await page.goto("/workflows");
  await expect(page.getByRole("link", { name: "Demo · Clasificar pedido" })).toBeVisible();

  await page.getByRole("link", { name: "Demo · Clasificar pedido" }).click();
  await expect(page.getByLabel("Definición del flujo")).toBeVisible();

  // Disparador de webhook: se crea una vez; después aparece como nodo de entrada del lienzo.
  await page.getByRole("tab", { name: "Disparadores" }).click();
  const triggerName = "Pedidos del ERP";
  await expect(page.getByRole("button", { name: "Nuevo disparador" })).toBeVisible();
  await page.waitForTimeout(600);
  if ((await page.getByRole("cell", { name: triggerName, exact: true }).count()) === 0) {
    await page.getByRole("button", { name: "Nuevo disparador" }).click();
    await page.getByLabel("Nombre").fill(triggerName);
    await page.getByRole("button", { name: "Crear", exact: true }).click();
    await page.getByRole("button", { name: "Ya lo copié" }).click();
  }
  await expect(page.getByRole("cell", { name: triggerName, exact: true })).toBeVisible();
  await page.waitForTimeout(500);
  await page.screenshot(shot("13-disparadores"));

  await page.getByRole("tab", { name: "Definición" }).click();
  await expect(page.getByTestId("flow-canvas")).toBeVisible();
  await expect(page.getByLabel(`Disparador ${triggerName}`)).toBeVisible();
  await page.waitForTimeout(800);
  await page.screenshot(shot("03-editor-flujo"));

  await page.getByRole("tab", { name: "Versiones" }).click();
  await page.getByRole("button", { name: "Abrir versión 1" }).click();
  await expect(page.getByRole("heading", { name: "Versión 1" })).toBeVisible();
  await expect(page.getByTestId("flow-canvas")).toBeVisible();
  await page.waitForTimeout(800);
  await page.screenshot(shot("14-versiones"));

  // Programaciones: la de 15 minutos ya se disparó sola (beat → barrido → ejecución).
  await page.goto("/workflows");
  await page.getByRole("link", { name: "Demo · Confirmación diferida" }).click();
  await page.getByRole("tab", { name: "Programaciones" }).click();
  await expect(page.getByText("Demo · Confirmación cada 15 minutos")).toBeVisible();
  await page.screenshot(shot("04-programaciones"));

  await page.goto("/runs");
  await expect(page.locator("[data-status]").first()).toBeVisible();
  await page.waitForTimeout(500);

  // Cobro VIP: la pasarela simulada falla dos veces con 503 y el motor reintenta.
  const openVipRun = async (status: string) => {
    await page.goto("/workflows");
    await page.getByRole("link", { name: "Demo · Cobro a cliente VIP" }).click();
    await page.getByRole("tab", { name: "Ejecuciones" }).click();
    await page.locator("tbody tr", { hasText: status }).first().locator("a").first().click();
  };
  await openVipRun("Completada");
  await expect(page.locator("[data-status=succeeded]").first()).toBeVisible();
  await page.getByRole("button", { name: /^\d+\. charge/ }).click();
  await page.waitForTimeout(800);
  await page.screenshot({ ...shot("06-detalle-ejecucion"), fullPage: true });

  // Cliente inexistente: error clasificado, sin reintentos inútiles.
  await openVipRun("Fallida");
  await expect(page.locator("[data-status=failed]").first()).toBeVisible();
  await page.waitForTimeout(500);
  await page.screenshot({ ...shot("07-ejecucion-fallida"), fullPage: true });

  // Webhooks salientes: una suscripción hacia la API simulada, un ping y su entrega.
  await page.goto("/webhooks");
  await expect(page.getByRole("heading", { name: "Webhooks" })).toBeVisible();
  const hookName = "Avisos a facturación";
  await page.waitForTimeout(600);
  if ((await page.getByRole("cell", { name: hookName, exact: true }).count()) === 0) {
    // Por API y no por el panel: el panel no deja elegir el secreto (lo genera el servidor).
    const login = await page.request.post("/api/v1/auth/login", { data: { email: "demo-admin@example.com", password: PASSWORD } });
    const token = ((await login.json()) as { access_token: string }).access_token;
    const created = await page.request.post("/api/v1/webhook-subscriptions", {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        name: hookName,
        url: "http://mock-partner:8080/webhooks/nexusflow",
        events: ["run.succeeded", "run.failed"],
        secret: process.env.E2E_WEBHOOK_SECRET ?? null,
      },
    });
    expect(created.status()).toBe(201);
    await page.reload();
  }
  await expect(page.getByRole("cell", { name: hookName, exact: true })).toBeVisible();
  await page.mouse.move(0, 0);
  await page.waitForTimeout(500);
  await page.screenshot(shot("11-webhooks"));

  await page.getByRole("button", { name: `Enviar ping a ${hookName}` }).click();
  await page.getByRole("button", { name: `Entregas de ${hookName}` }).click();
  await expect(page.getByRole("dialog").getByRole("cell", { name: /ping/ }).first()).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(6000); // el cajón refresca cada 5 s: la entrega llega a su estado final
  await page.screenshot(shot("15-entregas"));
  await page.keyboard.press("Escape");

  await page.goto("/credentials");
  await expect(page.getByText("demo_crm")).toBeVisible();
  await page.screenshot(shot("08-credenciales"));

  await page.goto("/audit");
  await page.waitForTimeout(900);
  await page.screenshot(shot("09-auditoria"));
});
