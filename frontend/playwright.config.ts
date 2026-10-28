import { defineConfig, devices } from "@playwright/test";

/**
 * E2E contra el stack real (nginx + API + worker + PostgreSQL + Redis).
 * - Docker:  E2E_BASE_URL=http://127.0.0.1:3000  (por defecto)
 * - Local:   pnpm build && pnpm preview  →  E2E_BASE_URL=http://127.0.0.1:4173
 * Requiere los datos de demostración: `nexusflow seed-demo` con NEXUSFLOW_DEMO_PASSWORD,
 * y la misma contraseña en E2E_PASSWORD.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
