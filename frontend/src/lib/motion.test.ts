import { renderHook } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { mediaMatching } from "../test/fakeCanvas";
import { liteMotion, motionAllowed, useMotionAllowed } from "./motion";

afterEach(() => vi.unstubAllGlobals());

test("sin respuesta del navegador no se anima", () => {
  expect(motionAllowed()).toBe(false);
  expect(liteMotion()).toBe(false);
  vi.stubGlobal("matchMedia", undefined);
  expect(motionAllowed()).toBe(false);
});

test("anima si no hay preferencia y detecta pantallas pequeñas", () => {
  vi.stubGlobal("matchMedia", mediaMatching("no-preference", "max-width"));
  expect(motionAllowed()).toBe(true);
  expect(liteMotion()).toBe(true);
  const { result, unmount } = renderHook(() => useMotionAllowed());
  expect(result.current).toBe(true);
  unmount();
});
