import type { RunnerState } from "../types";

const labels: Record<RunnerState, string> = {
  idle: "Aguardando",
  queued: "Na fila",
  running: "Executando",
  succeeded: "Concluído",
  failed: "Com falhas",
  offline: "Offline",
};

export function StatusBadge({ state }: { state: RunnerState }) {
  return (
    <span className={`status-badge status-${state}`}>
      <span />
      {labels[state]}
    </span>
  );
}
