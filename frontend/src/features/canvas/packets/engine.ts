/**
 * Motor de paquetes: estado de la simulación y dibujo en un contexto 2D. Sin DOM ni React:
 * el reloj avanza solo cuando se le llama, así que con el lienzo oculto el tiempo se congela.
 */
import { pointAt, type Point, type Polyline } from "./geometry";
import type { Arrival, PacketPlan } from "./timeline";

export const COLORS = {
  packet: "#22d3ee",
  bounce: "#fbbf24",
  ok: "#34d399",
  fail: "#fb7185",
  wait: "#38bdf8",
  core: "#e8fbff",
} as const;

/** Retícula de puntos del fondo de React Flow, en coordenadas del flujo. */
export interface DotGrid {
  gap: number;
  size: number;
}

export interface View {
  x: number;
  y: number;
  zoom: number;
  dpr: number;
  width: number;
  height: number;
}

interface Segment {
  from: number;
  to: number;
  ms: number;
  tint: string;
}

interface Packet {
  edgeId: string;
  start: number;
  bounces: number;
  arrival: Arrival;
  line: Polyline | null;
  segments: Segment[];
  waited: number;
}

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  born: number;
  life: number;
  color: string;
}

interface Ring {
  x: number;
  y: number;
  born: number;
  life: number;
  color: string;
}

export interface EngineOptions {
  lite: boolean;
  grid?: DotGrid;
  random?: () => number;
}

/** Velocidad en px del flujo por segundo. */
const SPEED = 320;
const MIN_SEGMENT_MS = 140;
/** Un rebote vuelve hasta este punto de la conexión antes de reintentar. */
const BOUNCE_BACK = 0.6;
const STREAM_MS = 900;
/** Si una conexión no aparece en el DOM en este tiempo, su paquete se descarta. */
const RESOLVE_TIMEOUT_MS = 3000;
const MAX_DT = 50;
const TRAIL = 6;
const TRAIL_GAP = 7;
const GLOW_RADIUS = 46;
const SPARKS = 14;

export class PacketEngine {
  time = 0;
  packets: Packet[] = [];
  particles: Particle[] = [];
  rings: Ring[] = [];
  private glow: CanvasImageSource | null = null;
  private streams: string[] = [];
  private nextStream = 0;
  private readonly random: () => number;
  private readonly resolve: (edgeId: string) => Polyline | null;
  private readonly opts: EngineOptions;

  constructor(resolve: (edgeId: string) => Polyline | null, opts: EngineOptions) {
    this.resolve = resolve;
    this.opts = opts;
    this.random = opts.random ?? Math.random;
  }

  /** Halo precalculado para la cabeza: un drawImage por paquete en lugar de un degradado. */
  setGlow(sprite: CanvasImageSource | null) {
    this.glow = sprite;
  }

  spawn(plans: PacketPlan[]) {
    for (const plan of plans) {
      this.packets.push({ edgeId: plan.edgeId, start: this.time + plan.at, bounces: plan.bounces, arrival: plan.arrival, line: null, segments: [], waited: 0 });
    }
  }

  setStreams(edgeIds: string[]) {
    if (this.streams.length === 0) this.nextStream = this.time;
    this.streams = edgeIds;
  }

  clear() {
    this.packets = [];
    this.particles = [];
    this.rings = [];
  }

  /** Hay algo que dibujar o que va a aparecer: el bucle debe seguir. */
  get busy(): boolean {
    return this.packets.length > 0 || this.particles.length > 0 || this.rings.length > 0 || this.streams.length > 0;
  }

  step(dtMs: number) {
    this.time += Math.min(Math.max(dtMs, 0), MAX_DT);
    const now = this.time;
    if (this.streams.length > 0 && now >= this.nextStream) {
      this.spawn(this.streams.map((edgeId) => ({ edgeId, at: 0, bounces: 0, arrival: "wait" as const })));
      this.nextStream = now + STREAM_MS;
    }

    const alive: Packet[] = [];
    for (const packet of this.packets) {
      if (now < packet.start) {
        alive.push(packet);
        continue;
      }
      if (!packet.line) {
        const line = this.resolve(packet.edgeId);
        if (!line) {
          packet.waited += dtMs;
          // Sin trazo todavía (React Flow mide los nodos antes de dibujar conexiones): espera.
          if (packet.waited < RESOLVE_TIMEOUT_MS) {
            packet.start = now;
            alive.push(packet);
          }
          continue;
        }
        packet.line = line;
        packet.segments = segmentsFor(line.length, packet.bounces);
        packet.start = now;
      }
      if (now - packet.start >= totalMs(packet.segments)) this.arrive(packet);
      else alive.push(packet);
    }
    this.packets = alive;
    this.particles = this.particles.filter((p) => now - p.born < p.life);
    this.rings = this.rings.filter((r) => now - r.born < r.life);
  }

  private arrive(packet: Packet) {
    const line = packet.line as Polyline;
    const end = pointAt(line, line.length);
    const now = this.time;
    if (packet.arrival === "fail" && !this.opts.lite) {
      for (let i = 0; i < SPARKS; i++) {
        const angle = this.random() * Math.PI * 2;
        const speed = 40 + this.random() * 110;
        this.particles.push({ x: end.x, y: end.y, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed, born: now, life: 550 + this.random() * 300, color: COLORS.fail });
      }
    }
    const color = packet.arrival === "fail" ? COLORS.fail : packet.arrival === "wait" ? COLORS.wait : COLORS.ok;
    this.rings.push({ x: end.x, y: end.y, born: now, life: packet.arrival === "wait" ? 450 : 650, color });
  }

  /** Cabeza del paquete, color del tramo actual y sentido. null si aún no salió. */
  head(packet: Packet): { point: Point; distance: number; tint: string; dir: number } | null {
    if (!packet.line || this.time < packet.start) return null;
    let elapsed = this.time - packet.start;
    for (const seg of packet.segments) {
      if (elapsed <= seg.ms) {
        const k = easeInOut(elapsed / seg.ms);
        const distance = (seg.from + (seg.to - seg.from) * k) * packet.line.length;
        return { point: pointAt(packet.line, distance), distance, tint: seg.tint, dir: Math.sign(seg.to - seg.from) || 1 };
      }
      elapsed -= seg.ms;
    }
    return null;
  }

  /** Hay algo visible ahora mismo (un paquete en marcha, un anillo o una chispa). */
  get visible(): boolean {
    return this.particles.length > 0 || this.rings.length > 0 || this.packets.some((p) => this.head(p) !== null);
  }

  draw(ctx: CanvasRenderingContext2D, view: View) {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, view.width * view.dpr, view.height * view.dpr);
    const s = view.dpr * view.zoom;
    ctx.setTransform(s, 0, 0, s, view.x * view.dpr, view.y * view.dpr);
    ctx.globalCompositeOperation = "lighter";

    const heads = this.packets.map((p) => ({ packet: p, head: this.head(p) })).filter((h) => h.head !== null);
    if (!this.opts.lite && this.opts.grid) {
      for (const { head } of heads) this.drawGridGlow(ctx, head!.point, this.opts.grid);
    }
    for (const { packet, head } of heads) {
      const { point, distance, tint, dir } = head!;
      const line = packet.line as Polyline;
      if (!this.opts.lite) {
        for (let i = TRAIL; i >= 1; i--) {
          const p = pointAt(line, distance - dir * i * TRAIL_GAP);
          ctx.globalAlpha = 0.5 * (1 - i / (TRAIL + 1));
          ctx.fillStyle = tint;
          circle(ctx, p.x, p.y, 3.6 * (1 - i / (TRAIL + 2)));
        }
      }
      ctx.globalAlpha = 1;
      if (this.glow) ctx.drawImage(this.glow, point.x - 18, point.y - 18, 36, 36);
      ctx.fillStyle = tint;
      circle(ctx, point.x, point.y, 4.4);
      ctx.fillStyle = COLORS.core;
      circle(ctx, point.x, point.y, 2);
    }

    for (const ring of this.rings) {
      const k = (this.time - ring.born) / ring.life;
      ctx.globalAlpha = (1 - k) * 0.9;
      ctx.strokeStyle = ring.color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(ring.x, ring.y, 5 + k * 22, 0, Math.PI * 2);
      ctx.stroke();
    }
    for (const p of this.particles) {
      const age = (this.time - p.born) / 1000;
      const k = (this.time - p.born) / p.life;
      // Frenado exponencial: la chispa sale rápida y se detiene.
      const travel = (1 - Math.exp(-age * 4)) / 4;
      ctx.globalAlpha = 1 - k;
      ctx.fillStyle = p.color;
      circle(ctx, p.x + p.vx * travel, p.y + p.vy * travel, 2 * (1 - k * 0.6));
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
  }

  /** Los puntos del fondo cercanos a un paquete se encienden un instante. */
  private drawGridGlow(ctx: CanvasRenderingContext2D, at: Point, grid: DotGrid) {
    // Centro de cada punto del patrón de React Flow: k·gap − gap/2 + size/2.
    const offset = -grid.gap / 2 + grid.size / 2;
    const x0 = Math.ceil((at.x - GLOW_RADIUS - offset) / grid.gap);
    const x1 = Math.floor((at.x + GLOW_RADIUS - offset) / grid.gap);
    const y0 = Math.ceil((at.y - GLOW_RADIUS - offset) / grid.gap);
    const y1 = Math.floor((at.y + GLOW_RADIUS - offset) / grid.gap);
    ctx.fillStyle = COLORS.packet;
    for (let i = x0; i <= x1; i++) {
      for (let j = y0; j <= y1; j++) {
        const x = i * grid.gap + offset;
        const y = j * grid.gap + offset;
        const d = Math.hypot(x - at.x, y - at.y);
        if (d >= GLOW_RADIUS) continue;
        const k = 1 - d / GLOW_RADIUS;
        ctx.globalAlpha = k * k * 0.85;
        circle(ctx, x, y, 1.1 + k * 0.6);
      }
    }
  }
}

export function segmentsFor(length: number, bounces: number): Segment[] {
  const ms = (from: number, to: number) => Math.max(MIN_SEGMENT_MS, (Math.abs(to - from) * length * 1000) / SPEED);
  const segments: Segment[] = [{ from: 0, to: 1, ms: ms(0, 1), tint: COLORS.packet }];
  for (let i = 0; i < bounces; i++) {
    segments.push({ from: 1, to: BOUNCE_BACK, ms: ms(1, BOUNCE_BACK), tint: COLORS.bounce });
    segments.push({ from: BOUNCE_BACK, to: 1, ms: ms(BOUNCE_BACK, 1), tint: COLORS.bounce });
  }
  return segments;
}

function totalMs(segments: Segment[]): number {
  return segments.reduce((sum, s) => sum + s.ms, 0);
}

function easeInOut(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
}

function circle(ctx: CanvasRenderingContext2D, x: number, y: number, r: number) {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fill();
}
