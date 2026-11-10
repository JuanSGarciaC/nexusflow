import { Skeleton } from "@mantine/core";
import { Suspense, lazy, useState, type ComponentProps } from "react";

import { parseJson } from "../workflows/templates";
import type { FlowDefinition } from "./layout";

// React Flow pesa decenas de kB: se descarga solo cuando una pantalla dibuja un lienzo.
const FlowCanvas = lazy(() => import("./FlowCanvas"));

type CanvasProps = ComponentProps<typeof FlowCanvas>;

export function LazyFlowCanvas(props: CanvasProps) {
  return (
    <Suspense fallback={<Skeleton h={props.height ?? 420} radius="lg" />}>
      <FlowCanvas {...props} />
    </Suspense>
  );
}

/** Lienzo que sigue a un JSON mientras se escribe. Si deja de ser válido, conserva el último válido. */
export function DefinitionPreview({ text, ...canvas }: { text: string } & Omit<CanvasProps, "definition">) {
  const [valid, setValid] = useState<{ text: string; definition: FlowDefinition } | null>(null);
  if (valid?.text !== text) {
    const parsed = parseJson(text);
    // Ajuste de estado durante el render (patrón de React para estado derivado): evita un efecto.
    if (parsed.ok && typeof parsed.value === "object" && parsed.value !== null) {
      setValid({ text, definition: parsed.value as FlowDefinition });
    }
  }
  return valid ? <LazyFlowCanvas definition={valid.definition} {...canvas} /> : null;
}
