/**
 * Capa de paquetes sobre el lienzo de React Flow: un <canvas> que no recibe eventos y dibuja
 * los datos recorriendo las conexiones. Solo se monta en modo ejecución y con movimiento permitido.
 * Pegamento con el DOM; la lógica está en timeline.ts y engine.ts.
 */
import { Button } from "@mantine/core";
import { IconPlayerPlay } from "@tabler/icons-react";
import { Panel, useStoreApi } from "@xyflow/react";
import { useEffect, useMemo, useRef, useState } from "react";

import { liteMotion } from "../../../lib/motion";
import type { FlowEdge } from "../layout";
import { traversedEdges, type NodeRun, type StepView } from "../runState";
import { COLORS, PacketEngine, type DotGrid } from "./engine";
import { sample, type Polyline } from "./geometry";
import { newlyTraversed, packetFor, replayPlan, streamingEdges } from "./timeline";
import classes from "./PacketLayer.module.css";

interface PacketLayerProps {
  edges: FlowEdge[];
  steps: StepView[];
  runs: Map<string, NodeRun>;
  grid: DotGrid;
}

const MAX_DPR = 2;

function glowSprite(): HTMLCanvasElement | null {
  const sprite = document.createElement("canvas");
  sprite.width = sprite.height = 64;
  const g = sprite.getContext("2d");
  if (!g) return null;
  const gradient = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  gradient.addColorStop(0, "rgba(34, 211, 238, 0.55)");
  gradient.addColorStop(0.4, "rgba(34, 211, 238, 0.18)");
  gradient.addColorStop(1, "rgba(34, 211, 238, 0)");
  g.fillStyle = gradient;
  g.fillRect(0, 0, 64, 64);
  return sprite;
}

export function PacketLayer({ edges, steps, runs, grid }: PacketLayerProps) {
  const store = useStoreApi();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [lite] = useState(liteMotion);
  const [engine] = useState(() => {
    const cache = new Map<string, { d: string; line: Polyline }>();
    const resolve = (edgeId: string): Polyline | null => {
      const root = store.getState().domNode;
      const id = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(edgeId) : edgeId;
      const path = root?.querySelector<SVGPathElement>(`.react-flow__edge[data-id="${id}"] .react-flow__edge-path`);
      const d = path?.getAttribute("d");
      if (!path || !d || typeof path.getTotalLength !== "function") return null;
      const hit = cache.get(edgeId);
      if (hit?.d === d) return hit.line;
      const line = sample(path.getTotalLength(), (at) => path.getPointAtLength(at));
      cache.set(edgeId, { d, line });
      return line;
    };
    return new PacketEngine(resolve, { lite, grid });
  });
  // kick arranca el bucle si hay algo que animar; lo rellena el efecto del bucle.
  const kick = useRef<() => void>(() => {});
  const seen = useRef<Set<string> | null>(null);
  const plan = useMemo(() => replayPlan(edges, steps, runs), [edges, steps, runs]);

  // Bucle de dibujo: rAF solo mientras hay paquetes y el lienzo está visible.
  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    const root = store.getState().domNode;
    if (!canvas || !ctx || !root) return;
    engine.setGlow(glowSprite());
    let raf = 0;
    let last = 0;
    let onScreen = true;
    let dirty = false;
    let size = { width: 0, height: 0, dpr: 1 };

    const frame = (now: number) => {
      raf = 0;
      engine.step(now - last);
      last = now;
      // Entre paquetes de un flujo continuo no hay nada que pintar: si el lienzo ya está
      // limpio, se ahorra el borrado y la composición de ese fotograma.
      const visibleNow = engine.visible;
      if (visibleNow || dirty) {
        const [x, y, zoom] = store.getState().transform;
        engine.draw(ctx, { x, y, zoom, ...size });
      }
      dirty = visibleNow;
      if (engine.busy && visible()) raf = requestAnimationFrame(frame);
    };
    const visible = () => onScreen && document.visibilityState !== "hidden";
    const start = () => {
      if (raf || !engine.busy || !visible()) return;
      last = performance.now();
      raf = requestAnimationFrame(frame);
    };
    const stop = () => {
      cancelAnimationFrame(raf);
      raf = 0;
    };
    kick.current = start;

    const resize = new ResizeObserver(() => {
      const box = root.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
      size = { width: box.width, height: box.height, dpr };
      canvas.width = Math.round(box.width * dpr);
      canvas.height = Math.round(box.height * dpr);
    });
    resize.observe(root);
    const intersection = new IntersectionObserver(([entry]) => {
      onScreen = entry?.isIntersecting ?? true;
      if (onScreen) start();
      else stop();
    });
    intersection.observe(root);
    const onVisibility = () => (visible() ? start() : stop());
    document.addEventListener("visibilitychange", onVisibility);
    start();
    return () => {
      stop();
      kick.current = () => {};
      resize.disconnect();
      intersection.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [engine, store]);

  // Reproducción al abrir (en móvil, solo con el botón) y paquetes nuevos en cada consulta.
  useEffect(() => {
    const traversed = traversedEdges(edges, runs);
    if (seen.current === null) {
      if (!lite) engine.spawn(plan);
    } else {
      const fresh = newlyTraversed(seen.current, traversed);
      engine.spawn(fresh.flatMap((id) => packetFor(edges.find((e) => e.id === id) as FlowEdge, runs) ?? []));
    }
    seen.current = traversed;
    engine.setStreams(streamingEdges(edges, runs));
    kick.current();
  }, [edges, runs, plan, engine, lite]);

  const replay = () => {
    engine.clear();
    engine.spawn(plan);
    kick.current();
  };

  return (
    <>
      <canvas ref={canvasRef} className={classes.layer} aria-hidden data-testid="packet-layer" />
      {plan.length > 0 && (
        <Panel position="top-right">
          <Button
            size="compact-xs"
            variant="light"
            color="cyan"
            leftSection={<IconPlayerPlay size={12} color={COLORS.packet} />}
            onClick={replay}
          >
            Repetir recorrido
          </Button>
        </Panel>
      )}
    </>
  );
}
