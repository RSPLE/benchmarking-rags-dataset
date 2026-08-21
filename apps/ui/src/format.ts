import type { RagPipeline } from "./types";

export function formatDuration(seconds: number | undefined): string {
  const value = Math.max(Math.round(seconds ?? 0), 0);
  if (!value) return "—";
  if (value < 60) return `${value}s`;
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const remainingSeconds = value % 60;
  if (hours) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  return `${minutes}m ${String(remainingSeconds).padStart(2, "0")}s`;
}

export function totalDuration(item: RagPipeline): number {
  const accumulated = item.runner.total_duration_seconds ?? item.runner.duration_seconds ?? 0;
  if (item.runner.state !== "running") return accumulated;
  return accumulated + (item.runner.duration_seconds ?? 0);
}
