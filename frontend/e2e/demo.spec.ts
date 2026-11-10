/**
 * Graba la demo del README (docs/demo/nexusflow-demo.gif) contra el stack real con los datos
 * de demostración. No forma parte de la suite normal: solo corre con RECORD_DEMO=1.
 *
 *   RECORD_DEMO=1 E2E_BASE_URL=... E2E_PASSWORD=... pnpm exec playwright test demo
 *
 * El vídeo (.webm) queda en test-results/; la conversión a GIF está en docs/demo.md.
 */
import { createHmac } from "node:crypto";

import { expect, test, type Page } from "@playwright/test";

import { PASSWORD, loginAs } from "./helpers";

test.skip(!process.env.RECORD_DEMO, "Solo para grabar la demo del README");
test.use({
  viewport: { width: 1280, height: 720 },
  colorScheme: "light",
  video: { mode: "on", size: { width: 1280, height: 720 } },
});
test.setTimeout(180_000);

// Rótulo fijo sobre la página: qué se está viendo y que los datos son ficticios.
async function caption(page: Page, text: string, ms = 2600) {
  await page.evaluate((t) => {
    let el = document.getElementById("demo-caption");
    if (!el) {
      el = document.createElement("div");
      el.id = "demo-caption";
      el.style.cssText =
        "position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:99999;max-width:1100px;" +
        "background:rgba(20,24,33,.92);color:#fff;font:600 19px/1.35 system-ui,sans-serif;padding:12px 20px;" +
        "border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.25);pointer-events:none;text-align:center";
      document.body.appendChild(el);
      const tag = document.createElement("div");
      tag.textContent = "DEMO · datos ficticios";
      tag.style.cssText =
        "position:fixed;top:17px;left:128px;z-index:99999;background:#e8590c;color:#fff;font:700 12px system-ui;" +
        "padding:4px 9px;border-radius:6px;letter-spacing:.04em;pointer-events:none";
      document.body.appendChild(tag);
    }
    el.textContent = t;
  }, text);
  await page.waitForTimeout(ms);
}

// Panel tipo terminal para mostrar las respuestas reales del webhook.
async function terminal(page: Page, lines: string[]) {
  await page.evaluate((ls) => {
    const box = document.createElement("pre");
    box.id = "demo-terminal";
    box.style.cssText =
      "position:fixed;left:50%;top:120px;transform:translateX(-50%);z-index:99998;width:900px;margin:0;" +
      "background:#0d1117;color:#c9d1d9;font:15px/1.6 ui-monospace,Consolas,monospace;padding:18px 22px;" +
      "border-radius:10px;box-shadow:0 10px 40px rgba(0,0,0,.35);white-space:pre-wrap";
    box.textContent = ls.join("\n");
    document.body.appendChild(box);
  }, lines);
}

async function runVip(page: Page, key: string) {
  await page.goto("/workflows");
  await page.getByRole("link", { name: "Demo · Cobro a cliente VIP" }).click();
  await page.getByRole("tab", { name: "Ejecutar" }).click();
  const input = page.getByLabel("Entrada (JSON)");
  await input.fill('{\n  "customer_id": "c-100",\n  "amount": 49.9\n}');
  await page.getByLabel("Clave de idempotencia (opcional)").pressSequentially(key, { delay: 40 });
  await page.waitForTimeout(500);
  await page.getByRole("button", { name: "Ejecutar", exact: true }).click();
  await page.waitForURL(/\/runs\/[0-9a-f-]{36}$/);
  return page.url();
}

test("demo del README", async ({ page, request }) => {
  const key = `demo-${Date.now()}`;

  await loginAs(page, "operator", "/");
  await caption(page, "NexusFlow ejecuta flujos que llaman a APIs externas que fallan. Entramos como operador.");

  // 1. Reintentos ante 503 con la misma Idempotency-Key.
  await caption(page, "Cobro a un cliente VIP: la pasarela simulada responde 503 las dos primeras veces.", 1800);
  const firstRun = await runVip(page, key);
  await expect(page.locator("[data-status=succeeded]").first()).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: /^\d+\. charge/ }).click();
  await page.waitForTimeout(400);
  // La tabla de intentos en el centro, por encima del rótulo.
  await page.getByText("HTTP 503").last().evaluate((el) => el.scrollIntoView({ block: "center" }));
  await caption(page, "Intento 1: 503 · intento 2: 503 · intento 3: éxito. Mismo Idempotency-Key → un solo cobro.", 4200);

  // 2. La misma clave devuelve la misma ejecución.
  await caption(page, "¿Y si el operador pulsa dos veces? Repetimos con la misma clave de idempotencia.", 2000);
  const secondRun = await runVip(page, key);
  expect(secondRun).toBe(firstRun);
  await caption(page, "Misma clave → la misma ejecución (misma URL). No se crea otra ni se cobra dos veces.", 3400);

  // 3. Webhook firmado, rechazado y reenviado: peticiones reales contra la API.
  const login = await request.post("/api/v1/auth/login", {
    data: { email: "demo-editor@example.com", password: PASSWORD },
  });
  const token = ((await login.json()) as { access_token: string }).access_token;
  const auth = { Authorization: `Bearer ${token}` };
  const flows = (await (await request.get("/api/v1/workflows?limit=50", { headers: auth })).json()) as {
    items: { id: string; name: string }[];
  };
  const flow = flows.items.find((w) => w.name === "Demo · Clasificar pedido")!;
  const trigger = (await (
    await request.post(`/api/v1/workflows/${flow.id}/triggers`, { headers: auth, data: { name: "erp-demo" } })
  ).json()) as { url_path: string; secret: string };
  const body = JSON.stringify({ order_id: "PED-1001", amount: 2500, country: "ES" });
  const eventId = `evt-${Date.now()}`;
  const send = async (secret: string) => {
    const t = Math.floor(Date.now() / 1000);
    const v1 = createHmac("sha256", secret).update(`${t}.${body}`).digest("hex");
    const r = await request.post(trigger.url_path, {
      data: body,
      headers: {
        "Content-Type": "application/json",
        "X-NexusFlow-Signature": `t=${t},v1=${v1}`,
        "X-NexusFlow-Event-Id": eventId,
      },
    });
    return { status: r.status(), json: (await r.json()) as Record<string, unknown> };
  };
  const bad = await send(`whsec_${"x".repeat(40)}`);
  const ok = await send(trigger.secret);
  const dup = await send(trigger.secret);
  expect([bad.status, ok.status, dup.status]).toEqual([401, 202, 200]);
  expect(dup.json).toEqual({ run_id: ok.json.run_id, duplicate: true });

  await page.goto("/runs");
  await expect(page.locator("[data-status]").first()).toBeVisible();
  await caption(page, "Ahora un ERP avisa de un pedido por webhook firmado con HMAC.", 1600);
  await terminal(page, [
    `$ POST ${trigger.url_path}   (pedido PED-1001, evento ${eventId})`,
    "",
    `1. Firma incorrecta    → ${bad.status}  no se crea nada`,
    `2. Firma correcta      → ${ok.status}  run_id ${String(ok.json.run_id).slice(0, 8)}…  duplicate: false`,
    `3. Mismo evento (bis)  → ${dup.status}  run_id ${String(dup.json.run_id).slice(0, 8)}…  duplicate: true`,
  ]);
  await caption(page, "El reenvío del mismo evento devuelve la misma ejecución: deduplicado por id de evento.", 5000);
  await page.evaluate(() => document.getElementById("demo-terminal")?.remove());

  // 4. Permisos: el viewer solo lee.
  await page.getByRole("button", { name: "Salir" }).click();
  await loginAs(page, "viewer", "/workflows");
  await page.getByRole("link", { name: "Demo · Cobro a cliente VIP" }).click();
  await expect(page.getByRole("tab", { name: "Ejecutar" })).toHaveCount(0);
  await caption(page, "Un viewer ve flujos y ejecuciones, pero no puede ejecutar: el backend lo aplica en cada endpoint.", 3600);

  // 5. Credenciales y auditoría como admin.
  await page.getByRole("button", { name: "Salir" }).click();
  await loginAs(page, "admin", "/credentials");
  await expect(page.getByText("demo_crm")).toBeVisible();
  await caption(page, "Credenciales cifradas y de solo escritura: solo se ven metadatos y una pista.", 3200);
  await page.goto("/audit");
  await expect(page.getByText("webhook.trigger_created").first()).toBeVisible();
  await caption(page, "Auditoría de solo inserción: el webhook, las ejecuciones y los inicios de sesión quedan registrados.", 3600);
  await caption(page, "Cada comportamiento de esta demo tiene una prueba automatizada contra PostgreSQL y Redis reales.", 3200);
});
