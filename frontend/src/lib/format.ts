const dateTime = new Intl.DateTimeFormat("es", { dateStyle: "short", timeStyle: "medium" });

export function formatDate(value: string | null | undefined): string {
  return value ? dateTime.format(new Date(value)) : "—";
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 2 : 1)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${Math.round(seconds % 60)} s`;
}

export function formatPercent(rate: number | null | undefined): string {
  return rate === null || rate === undefined ? "—" : `${(rate * 100).toFixed(1)} %`;
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}
