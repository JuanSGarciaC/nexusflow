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
  delivered: "green",
  dead: "red",
};

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
};

export function statusLabel(status: string): string {
  return LABELS[status] ?? status;
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge color={COLORS[status] ?? "gray"} variant="light" data-status={status}>
      {statusLabel(status)}
    </Badge>
  );
}
