/**
 * Lienzo de un flujo: nodos conectados, arrastre y zoom. Solo lectura.
 * Con `steps` pasa a modo ejecución y colorea y anima lo que ocurrió de verdad.
 * Export por defecto para cargarlo con lazy(): React Flow no debe entrar en el chunk inicial.
 */
import "@xyflow/react/dist/style.css";

import { Text } from "@mantine/core";
import { Background, BackgroundVariant, Controls, MarkerType, MiniMap, ReactFlow, type Edge, type Node } from "@xyflow/react";
import { useMemo, useState } from "react";

import classes from "./FlowCanvas.module.css";
import { COL_W, ROW_H, connectedPath, layoutFlow, type FlowDefinition } from "./layout";
import { nodeRuns, traversedEdges, type StepView } from "./runState";
import { StepNode, type StepNodeData } from "./StepNode";

export interface CanvasTrigger {
  id: string;
  label: string;
  kind: "manual" | "cron" | "webhook";
}

interface FlowCanvasProps {
  definition: FlowDefinition;
  /** Pasos de una ejecución. Presente (aunque vacío) = modo ejecución. */
  steps?: StepView[];
  /** Nodos de entrada, conectados al paso inicial. */
  triggers?: CanvasTrigger[];
  onSelectStep?: (stepId: string) => void;
  height?: number;
}

const NODE_W = 210;
const NODE_H = 72;
// En flujos pequeños el minimapa no orienta y tapa nodos: solo aparece a partir de este tamaño.
const MINIMAP_FROM = 9;
const NODE_TYPES = { step: StepNode };
const NO_TRIGGERS: CanvasTrigger[] = [];
const EDGE_LABELS = { next: undefined, true: "sí", false: "no" } as const;
const STATUS_COLORS: Record<string, string> = {
  succeeded: "#34d399",
  failed: "#fb7185",
  running: "#38bdf8",
  retry_scheduled: "#fbbf24",
  needs_review: "#fbbf24",
};

export default function FlowCanvas({ definition, steps, triggers = NO_TRIGGERS, onSelectStep, height = 420 }: FlowCanvasProps) {
  const [hovered, setHovered] = useState<string | null>(null);
  const layout = useMemo(() => layoutFlow(definition), [definition]);
  const runs = useMemo(() => (steps ? nodeRuns(steps) : null), [steps]);

  const { nodes, edges } = useMemo(() => {
    const path = hovered ? connectedPath(layout.edges, hovered) : null;
    const traversed = runs ? traversedEdges(layout.edges, runs) : null;
    const startId = layout.nodes.find((n) => n.level === 0 && n.reachable)?.id;

    const stepNodes: Node<StepNodeData>[] = layout.nodes.map((n) => ({
      id: n.id,
      type: "step",
      position: { x: n.x, y: n.y },
      width: NODE_W,
      height: NODE_H,
      data: {
        label: n.label,
        kind: n.type,
        trigger: false,
        run: runs ? (runs.get(n.id) ?? null) : undefined,
        dimmed: path !== null && !path.nodes.has(n.id),
        reachable: n.reachable,
        onSelect: onSelectStep && (() => onSelectStep(n.id)),
        onHover: (hovering) => setHovered(hovering ? n.id : null),
      },
    }));
    const triggerNodes: Node<StepNodeData>[] = triggers.map((trigger, index) => ({
      id: `trigger:${trigger.id}`,
      type: "step",
      position: { x: -COL_W, y: index * ROW_H },
      width: NODE_W,
      height: NODE_H,
      data: { label: trigger.label, kind: trigger.kind, trigger: true, dimmed: path !== null, reachable: true, onHover: () => {} },
    }));

    const flowEdges: Edge[] = layout.edges.map((e) => {
      const walked = traversed?.has(e.id) ?? false;
      const faded = (path !== null && !path.edges.has(e.id)) || (traversed !== null && !walked);
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        type: "smoothstep",
        label: EDGE_LABELS[e.kind],
        animated: walked,
        className: walked ? classes.walked : undefined,
        style: { opacity: faded ? 0.25 : 1 },
        markerEnd: { type: MarkerType.ArrowClosed },
      };
    });
    const triggerEdges: Edge[] = startId
      ? triggers.map((trigger) => ({
          id: `trigger:${trigger.id}->${startId}`,
          source: `trigger:${trigger.id}`,
          target: startId,
          type: "smoothstep",
          className: classes.triggerEdge,
          style: { opacity: path !== null || traversed !== null ? 0.25 : 1 },
        }))
      : [];
    return { nodes: [...triggerNodes, ...stepNodes], edges: [...triggerEdges, ...flowEdges] };
  }, [layout, runs, hovered, triggers, onSelectStep]);

  if (layout.nodes.length === 0) {
    return (
      <div className={classes.canvas} style={{ height: 120 }}>
        <Text c="dimmed" size="sm" ta="center" pt={48}>
          Este flujo aún no tiene pasos
        </Text>
      </div>
    );
  }

  return (
    <div className={classes.canvas} style={{ height }} data-motion="" data-testid="flow-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        colorMode="dark"
        fitView
        fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
        minZoom={0.3}
        maxZoom={1.6}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        aria-label="Lienzo del flujo"
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} color="rgba(255,255,255,0.09)" />
        <Controls showInteractive={false} position="bottom-left" />
        {nodes.length >= MINIMAP_FROM && (
          <MiniMap
            pannable
            zoomable
            position="bottom-right"
            nodeColor={(node) => STATUS_COLORS[(node.data as StepNodeData).run?.status ?? ""] ?? "#3b3f58"}
            maskColor="rgba(7,8,13,0.7)"
          />
        )}
      </ReactFlow>
    </div>
  );
}
