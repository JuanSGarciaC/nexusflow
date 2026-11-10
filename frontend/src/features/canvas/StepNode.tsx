import { Tooltip } from "@mantine/core";
import {
  IconBolt,
  IconCalendarTime,
  IconClock,
  IconGitBranch,
  IconQuestionMark,
  IconTransform,
  IconWebhook,
  IconWorld,
  type Icon,
} from "@tabler/icons-react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";

import { statusLabel } from "../../components/StatusBadge";
import { formatDuration } from "../../lib/format";
import classes from "./FlowCanvas.module.css";
import type { NodeRun } from "./runState";

const TYPES: Record<string, { icon: Icon; caption: string }> = {
  http_request: { icon: IconWorld, caption: "Petición HTTP" },
  transform: { icon: IconTransform, caption: "Transformación" },
  delay: { icon: IconClock, caption: "Espera" },
  condition: { icon: IconGitBranch, caption: "Condición" },
  manual: { icon: IconBolt, caption: "Disparador" },
  cron: { icon: IconCalendarTime, caption: "Programación" },
  webhook: { icon: IconWebhook, caption: "Webhook entrante" },
};

export type StepNodeData = {
  label: string;
  /** Tipo de paso, o clase de disparador. */
  kind: string;
  trigger: boolean;
  /** undefined = modo definición; null = modo ejecución y el paso no tiene registro. */
  run?: NodeRun | null;
  dimmed: boolean;
  reachable: boolean;
  onSelect?: () => void;
  onHover: (hovering: boolean) => void;
};

function accessibleName({ label, trigger, run }: StepNodeData): string {
  if (trigger) return `Disparador ${label}`;
  if (run === undefined) return `Paso ${label}`;
  if (run === null) return `Paso ${label}: no ejecutado`;
  return `Paso ${label}: ${statusLabel(run.status).toLowerCase()}, ${run.attempts} de ${run.maxAttempts} intentos`;
}

function RunSummary({ run }: { run: NodeRun }) {
  return (
    <div className={classes.tip}>
      <strong>{statusLabel(run.status)}</strong>
      <span>Duración: {formatDuration(run.durationMs)}</span>
      <span>
        Intentos: {run.attempts} de {run.maxAttempts}
      </span>
      {run.error && <span className={classes.tipError}>{run.error}</span>}
    </div>
  );
}

export function StepNode({ data }: NodeProps<Node<StepNodeData>>) {
  const { icon: TypeIcon, caption } = TYPES[data.kind] ?? { icon: IconQuestionMark, caption: data.kind };
  const body = (
    <div
      className={classes.node}
      role={data.trigger ? "img" : "button"}
      tabIndex={data.trigger ? undefined : 0}
      aria-label={accessibleName(data)}
      data-kind={data.kind}
      // data-run y no data-status: ese atributo es de StatusBadge y las pruebas E2E lo buscan.
      data-run={data.run === null ? "skipped" : data.run?.status}
      data-dimmed={data.dimmed || undefined}
      data-unreachable={!data.reachable || undefined}
      onClick={data.onSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          data.onSelect?.();
        }
      }}
      onMouseEnter={() => data.onHover(true)}
      onMouseLeave={() => data.onHover(false)}
      onFocus={() => data.onHover(true)}
      onBlur={() => data.onHover(false)}
    >
      {!data.trigger && <Handle type="target" position={Position.Left} className={classes.handle} />}
      <span className={classes.chip} aria-hidden>
        <TypeIcon size={18} stroke={1.8} />
      </span>
      <span className={classes.text}>
        <span className={classes.label}>{data.label}</span>
        <span className={classes.caption}>{data.reachable ? caption : "Sin camino desde el inicio"}</span>
      </span>
      {data.run && data.run.attempts > 1 && (
        <span className={classes.attempts} aria-hidden>
          {data.run.attempts}/{data.run.maxAttempts}
        </span>
      )}
      {data.run && <span className={classes.dot} aria-hidden />}
      <Handle type="source" position={Position.Right} className={classes.handle} />
    </div>
  );
  return data.run ? (
    <Tooltip label={<RunSummary run={data.run} />} position="top" withArrow multiline>
      {body}
    </Tooltip>
  ) : (
    body
  );
}
