import { expect, type Page } from "@playwright/test";

export const PASSWORD = process.env.E2E_PASSWORD ?? "";
if (!PASSWORD) throw new Error("Define E2E_PASSWORD (la misma que NEXUSFLOW_DEMO_PASSWORD del seed)");

export type DemoRole = "admin" | "editor" | "operator" | "viewer";

export async function loginAs(page: Page, role: DemoRole, path = "/"): Promise<void> {
  await page.goto(path);
  await page.getByLabel("Email").fill(`demo-${role}@example.com`);
  await page.getByLabel("Contraseña").fill(PASSWORD);
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page.getByRole("button", { name: "Salir" })).toBeVisible();
}

export function nav(page: Page) {
  return page.getByRole("navigation", { name: "Navegación principal" });
}
