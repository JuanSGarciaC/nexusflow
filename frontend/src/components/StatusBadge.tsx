import { Badge } from "@mantine/core";

const COLORS: Record<string, string> = {
  pending: "gray",
  running: "blue",
  retry_scheduled: "yellow",
  needs_review: "orange",
  succeeded: "green",
  failed: "red",
  timed_out: "red",
  cancelled: "dark",
  draft: "gray",
  published: "green",
  active: "green",
  archived: "dark",
  delivering: "blue",
  delivered: "green",
  dead: "red",
};

// Estados en los que algo está ocurriendo ahora mismo: su punto late.
const LIVE = new Set(["running", "delivering", "retry_scheduled"]);

const LABELS: Record<string, string> = {
  pending: "Pendiente",
  running: "En curso",
  retry_scheduled: "Reintento programado",
  needs_review: "Revisión manual",
  succeeded: "Completada",
  failed: "Fallida",
  timed_out: "Tiempo agotado",
  cancelled: "Cancelada",
  draft: "Borrador",
  published: "Publicada",
  active: "Activo",
  archived: "Archivado",
  delivering: "Entregando",
  delivered: "Entregada",
  dead: "Agotada",
};

export function statusLabel(status: string): string {
  return LABELS[status] ?? status;
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge
      color={COLORS[status] ?? "gray"}
      variant="light"
      data-status={status}
      style={{ flexShrink: 0, minWidth: "max-content" }}
      leftSection={<span className="nf-status-dot" data-motion={LIVE.has(status) ? "" : undefined} aria-hidden />}
    >
      {statusLabel(status)}
    </Badge>
  );
}
