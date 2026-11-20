/** Trazos muestreados: una conexión SVG convertida en polilínea con longitudes acumuladas. */

export interface Point {
  x: number;
  y: number;
}

export interface Polyline {
  xs: Float32Array;
  ys: Float32Array;
  /** Longitud acumulada hasta cada punto. */
  acc: Float32Array;
  length: number;
}

const STEP_PX = 6;

/** Lectura acotada: con noUncheckedIndexedAccess un índice puede salirse. */
function at(values: Float32Array, i: number): number {
  return values[i] ?? 0;
}

/** Muestrea un trazo de longitud `length` cada pocos píxeles. `at` es getPointAtLength. */
export function sample(length: number, pointAtLength: (distance: number) => Point): Polyline {
  const count = Math.max(2, Math.ceil(length / STEP_PX) + 1);
  const xs = new Float32Array(count);
  const ys = new Float32Array(count);
  const acc = new Float32Array(count);
  let total = 0;
  for (let i = 0; i < count; i++) {
    const p = pointAtLength((length * i) / (count - 1));
    xs[i] = p.x;
    ys[i] = p.y;
    if (i > 0) total += Math.hypot(p.x - at(xs, i - 1), p.y - at(ys, i - 1));
    acc[i] = total;
  }
  return { xs, ys, acc, length: total };
}

/** Punto a la distancia `d` del origen (acotada a la polilínea). Búsqueda binaria. */
export function pointAt(line: Polyline, d: number): Point {
  const n = line.xs.length;
  if (d <= 0 || line.length === 0) return { x: at(line.xs, 0), y: at(line.ys, 0) };
  if (d >= line.length) return { x: at(line.xs, n - 1), y: at(line.ys, n - 1) };
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (at(line.acc, mid) < d) lo = mid;
    else hi = mid;
  }
  const a = at(line.acc, lo);
  const k = (d - a) / (at(line.acc, hi) - a || 1);
  const x = at(line.xs, lo);
  const y = at(line.ys, lo);
  return { x: x + (at(line.xs, hi) - x) * k, y: y + (at(line.ys, hi) - y) * k };
}
