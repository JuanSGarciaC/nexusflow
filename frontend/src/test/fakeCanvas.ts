/** Contexto 2D falso: acepta cualquier llamada y la registra. jsdom no implementa canvas. */
export function fakeContext() {
  const calls: string[] = [];
  const state: Record<string, unknown> = {};
  const ctx = new Proxy(state, {
    get(target, prop: string) {
      if (prop in target) return target[prop];
      return (...args: unknown[]) => {
        calls.push(prop);
        if (prop === "createRadialGradient") return { addColorStop: () => {} };
        return args.length >= 0 ? undefined : undefined;
      };
    },
    set(target, prop: string, value) {
      target[prop] = value;
      return true;
    },
  });
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls };
}

/** matchMedia que responde true a las consultas indicadas. */
export function mediaMatching(...matching: string[]) {
  return (query: string) => ({
    matches: matching.some((m) => query.includes(m)),
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  });
}
