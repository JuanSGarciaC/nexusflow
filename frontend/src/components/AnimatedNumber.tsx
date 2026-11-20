import { useLayoutEffect, useRef } from "react";

import { motionAllowed } from "../lib/motion";

const DURATION_MS = 700;

interface AnimatedNumberProps {
  value: number | null;
  format: (value: number | null) => string;
}

/**
 * Cifra que cuenta desde el valor anterior (o desde cero) hasta el nuevo.
 * El DOM lleva siempre el valor final que pinta React; el recuento solo reescribe el texto
 * mientras dura, así que lectores de pantalla, pruebas y JS deshabilitado ven la cifra real.
 * Un refresco con el mismo valor no anima nada.
 */
export function AnimatedNumber({ value, format }: AnimatedNumberProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const shown = useRef<number>(0);

  // Efecto de layout: el valor de partida se escribe antes de pintar, sin un fotograma con la cifra final.
  useLayoutEffect(() => {
    const el = ref.current;
    const from = shown.current;
    if (value === null) return;
    shown.current = value;
    if (!el) return;
    if (from === value || !motionAllowed()) {
      // Si se cortó un recuento, React pudo no reescribir el texto: se fija el valor final.
      el.textContent = format(value);
      return;
    }
    el.textContent = format(from);
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      // El sello de rAF puede ser anterior a performance.now(): se acota a [0, 1].
      const k = Math.min(1, Math.max(0, (now - start) / DURATION_MS));
      const eased = 1 - (1 - k) ** 3;
      el.textContent = format(from + (value - from) * eased);
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    // Sin reescribir el texto al limpiar: React ya pintó el valor nuevo antes de esta limpieza.
    return () => cancelAnimationFrame(raf);
  }, [value, format]);

  return <span ref={ref}>{format(value)}</span>;
}
