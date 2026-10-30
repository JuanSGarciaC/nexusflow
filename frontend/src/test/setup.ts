import "@testing-library/jest-dom/vitest";

import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => cleanup());

// Las rutas se cargan con React.lazy: la primera importación (Mantine + gráficos) puede tardar
// más del segundo por defecto en una máquina lenta o en frío. Es un tope, no una espera fija:
// las pruebas terminan en cuanto el elemento aparece.
configure({ asyncUtilTimeout: 5000 });

// jsdom no implementa estas APIs que usa Mantine.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }),
});
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
window.HTMLElement.prototype.scrollIntoView = () => {};
// jsdom tampoco implementa document.fonts (lo usa el Textarea con autosize de Mantine).
if (!("fonts" in document)) {
  Object.defineProperty(document, "fonts", {
    value: { addEventListener: () => {}, removeEventListener: () => {}, ready: Promise.resolve() },
  });
}
