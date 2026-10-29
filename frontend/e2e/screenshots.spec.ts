/**
 * Genera las capturas de docs/screenshots a partir del stack real con datos de demostración.
 * No forma parte de la suite normal: solo corre con CAPTURE_SCREENSHOTS=1.
 *
 *   CAPTURE_SCREENSHOTS=1 E2E_BASE_URL=... E2E_PASSWORD=... pnpm exec playwright test screenshots
 */
import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { loginAs } from "./helpers";

test.skip(!process.env.CAPTURE_SCREENSHOTS, "Solo para regenerar las capturas del README");
test.use({ viewport: { width: 1360, height: 820 }, colorScheme: "light" });

const OUT = fileURLToPath(new URL("../../docs/screenshots", import.meta.url));
const shot = (name: string) => ({ path: path.join(OUT, `${name}.png`), animations: "disabled" as const });

test("capturas del panel", async ({ page }) => {
  await loginAs(page, "admin", "/");
  await expect(page.getByText("Tasa de éxito").first()).toBeVisible();
  await page.mouse.move(0, 0); // sin tooltips del gráfico
  await page.waitForTimeout(1200); // gráfico y tablas
  await page.screenshot(shot("01-panel"));

  await page.goto("/workflows");
  await expect(page.getByRole("link", { name: "Demo · Clasificar pedido" })).toBeVisible();

  await page.getByRole("link", { name: "Demo · Clasificar pedido" }).click();
  await expect(page.getByLabel("Definición del flujo")).toBeVisible();
  await page.waitForTimeout(500);
  await page.screenshot(shot("03-editor-flujo"));

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
  await page.getByRole("button", { name: /charge/ }).click();
  await page.waitForTimeout(800);
  await page.screenshot({ ...shot("06-detalle-ejecucion"), fullPage: true });

  // Cliente inexistente: error clasificado, sin reintentos inútiles.
  await openVipRun("Fallida");
  await expect(page.locator("[data-status=failed]").first()).toBeVisible();
  await page.waitForTimeout(500);
  await page.screenshot({ ...shot("07-ejecucion-fallida"), fullPage: true });

  await page.goto("/credentials");
  await expect(page.getByText("demo_crm")).toBeVisible();
  await page.screenshot(shot("08-credenciales"));

  await page.goto("/audit");
  await page.waitForTimeout(900);
  await page.screenshot(shot("09-auditoria"));
});
