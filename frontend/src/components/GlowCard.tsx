import { Paper, type PaperProps } from "@mantine/core";
import type { PointerEvent, ReactNode } from "react";

import classes from "./GlowCard.module.css";

const MAX_TILT = 4;

interface GlowCardProps extends PaperProps {
  children: ReactNode;
  /** Inclinación 3D leve hacia el cursor. Solo para tarjetas de cifras, nunca tablas ni formularios. */
  tilt?: boolean;
  /** Borde de degradado que gira sin parar. */
  spin?: boolean;
}

/** Tarjeta con borde iluminado y un foco de luz que sigue al cursor. */
export function GlowCard({ children, tilt = false, spin = true, className, ...paper }: GlowCardProps) {
  // Se escribe en variables CSS y no en estado: un render por cada movimiento del cursor sería caro.
  function follow(event: PointerEvent<HTMLDivElement>) {
    const el = event.currentTarget;
    const box = el.getBoundingClientRect();
    const x = event.clientX - box.left;
    const y = event.clientY - box.top;
    el.style.setProperty("--mx", `${x}px`);
    el.style.setProperty("--my", `${y}px`);
    if (tilt && box.width > 0 && box.height > 0) {
      el.style.setProperty("--ry", `${((x / box.width) * 2 - 1) * MAX_TILT}deg`);
      el.style.setProperty("--rx", `${(1 - (y / box.height) * 2) * MAX_TILT}deg`);
    }
  }

  function rest(event: PointerEvent<HTMLDivElement>) {
    event.currentTarget.style.removeProperty("--rx");
    event.currentTarget.style.removeProperty("--ry");
  }

  return (
    <Paper
      p="md"
      radius="lg"
      {...paper}
      className={[classes.card, className].filter(Boolean).join(" ")}
      data-testid="glow-card"
      data-motion=""
      data-spin={spin || undefined}
      data-tilt={tilt || undefined}
      onPointerMove={follow}
      onPointerLeave={rest}
    >
      {children}
    </Paper>
  );
}
