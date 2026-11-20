/**
 * Preferencias de movimiento. Se pregunta por "no-preference" (y no por "reduce") a propósito:
 * si el navegador no sabe responder, o en jsdom, la respuesta es "sin animación".
 */
import { useSyncExternalStore } from "react";

const MOTION_OK = "(prefers-reduced-motion: no-preference)";
// Pantallas estrechas o táctiles: versión ligera de los efectos caros.
const LITE = "(max-width: 599px), (pointer: coarse)";

function query(media: string): MediaQueryList | null {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(media) : null;
}

export function motionAllowed(): boolean {
  return query(MOTION_OK)?.matches ?? false;
}

export function liteMotion(): boolean {
  return query(LITE)?.matches ?? false;
}

function subscribe(onChange: () => void) {
  const list = query(MOTION_OK);
  list?.addEventListener("change", onChange);
  return () => list?.removeEventListener("change", onChange);
}

/** true si se puede animar. Reacciona si el usuario cambia la preferencia con la página abierta. */
export function useMotionAllowed(): boolean {
  return useSyncExternalStore(subscribe, motionAllowed, () => false);
}
