/**
 * Recorrido de la demo: guarda fotogramas con un rótulo explicativo; scripts/make-demo-gif.py
 * los une en docs/demo.gif. No forma parte de la suite normal: solo corre con CAPTURE_DEMO=1.
 *
 *   CAPTURE_DEMO=1 FRAMES_DIR=... E2E_BASE_URL=... E2E_PASSWORD=... pnpm exec playwright test demo-gif
 */
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

import { loginAs } from "./helpers";

test.skip(!process.env.CAPTURE_DEMO, "Solo para regenerar docs/demo.gif");
test.use({ viewport: { width: 1280, height: 760 }, colorScheme: "light" });

const FRAMES_DIR = process.env.FRAMES_DIR ?? "";
if (process.env.CAPTURE_DEMO && !FRAMES_DIR) throw new Error("Define FRAMES_DIR");

const frames: { file: string; ms: number }[] = [];

async function snap(page: Page, caption: string, ms = 2200): Promise<void> {
  await page.evaluate((text) => {
    let el = document.getElementById("demo-caption");
    if (!el) {
      el = document.createElement("div");
      el.id = "demo-caption";
      el.style.cssText =
        "position:fixed;left:0;right:0;bottom:0;z-index:99999;background:#111827;color:#fff;" +
        "font:600 19px system-ui,sans-serif;padding:14px 24px;";
      document.body.appendChild(el);
    }
    el.textContent = text;
  }, caption);
  await page.mouse.move(0, 0); // sin tooltips
  await page.waitForTimeout(500);
  const file = `frame-${String(frames.length).padStart(2, "0")}.png`;
  await page.screenshot({ path: path.join(FRAMES_DIR, file), animations: "disabled" });
  frames.push({ file, ms });
}

test("recorrido de la demo", async ({ page }) => {
  fs.mkdirSync(FRAMES_DIR, { recursive: true });

  await loginAs(page, "admin", "/");
  await expect(page.getByText("Tasa de éxito").first()).toBeVisible();
  await page.waitForTimeout(1200);
  await snap(page, "Panel: volumen, tasa de éxito y latencias p50/p95, calculados en PostgreSQL");

  await page.goto("/workflows");
  await page.getByRole("link", { name: "Demo · Clasificar pedido" }).click();
  await expect(page.getByLabel("Definición del flujo")).toBeVisible();
  await snap(page, "Los flujos son definiciones JSON versionadas e inmutables");

  await page.getByRole("tab", { name: "Ejecutar" }).click();
  await page.getByLabel("Entrada (JSON)").fill(JSON.stringify({ order_id: "DEMO-1", amount: 5000, country: "CO" }));
  await snap(page, "Se lanza una ejecución con una entrada (y clave de idempotencia opcional)", 2000);
  await page.getByRole("button", { name: "Ejecutar", exact: true }).click();
  await expect(page).toHaveURL(/\/runs\//);
  await expect(page.locator("[data-status=succeeded]").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText('"decision": "revisión"').last()).toBeVisible();
  await snap(page, "El worker la ejecuta: importe ≥ 1000, la condición la envía a revisión", 2800);

  const openVipRun = async (status: string) => {
    await page.goto("/workflows");
    await page.getByRole("link", { name: "Demo · Cobro a cliente VIP" }).click();
    await page.getByRole("tab", { name: "Ejecuciones" }).click();
    await page.locator("tbody tr", { hasText: status }).first().locator("a").first().click();
  };
  await openVipRun("Completada");
  await expect(page.locator("[data-status=succeeded]").first()).toBeVisible();
  await page.getByRole("button", { name: /charge/ }).click();
  await page.waitForTimeout(600);
  await snap(page, "Cobro VIP: la pasarela falla dos veces con 503; el motor reintenta con la misma Idempotency-Key", 3600);

  await openVipRun("Fallida");
  await expect(page.locator("[data-status=failed]").first()).toBeVisible();
  await snap(page, "Cliente inexistente: un 404 se clasifica como error definitivo y no se reintenta", 3200);

  await page.goto("/credentials");
  await expect(page.getByText("demo_crm")).toBeVisible();
  await snap(page, "Credenciales de solo escritura: se ven metadatos y una pista, nunca el secreto", 3000);

  await page.goto("/audit");
  await page.waitForTimeout(900);
  await snap(page, "Auditoría de solo inserción: quién hizo qué, desde qué IP y cuándo", 3000);

  fs.writeFileSync(path.join(FRAMES_DIR, "frames.json"), JSON.stringify(frames));
});
