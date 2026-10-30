/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// En desarrollo, Vite reenvía /api y /health al backend: el navegador ve un único origen,
// así la cookie de refresh (SameSite=Strict, Path=/api/v1/auth) funciona sin CORS.
// En producción hace lo mismo nginx (frontend/nginx.conf).
const apiTarget = process.env.NEXUSFLOW_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: false },
      "/health": { target: apiTarget, changeOrigin: false },
    },
  },
  build: {
    sourcemap: true,
    chunkSizeWarningLimit: 1500,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    restoreMocks: true,
    // Las pruebas E2E las ejecuta Playwright, no Vitest.
    exclude: ["e2e/**", "node_modules/**"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      // Tipos generados, punto de entrada y propias pruebas no cuentan.
      exclude: ["src/**/*.test.*", "src/test/**", "src/api/schema.d.ts", "src/main.tsx", "src/vite-env.d.ts"],
      reporter: ["text-summary", "text"],
      // SUELO anti-regresión, no objetivo: unos puntos por debajo de lo medido (74,2 %
      // sentencias, 63,8 % ramas, 63,6 % funciones, 74,8 % líneas). Siguen flojas las listas de
      // ejecuciones y usuarios y el alta de flujos; ver docs/performance.md.
      // Súbelo cuando añadas pruebas; nunca lo bajes para que pase un cambio.
      thresholds: { statements: 72, branches: 61, functions: 61, lines: 72 },
    },
  },
});
