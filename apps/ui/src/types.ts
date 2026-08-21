export type RunnerState = "idle" | "queued" | "running" | "succeeded" | "failed" | "offline";

export interface RunnerStatus {
  available: boolean;
  state: RunnerState;
  started_at?: string | null;
  finished_at?: string | null;
  returncode?: number | null;
  logs: string[];
}

export interface BenchmarkResult {
  total: number;
  success: number;
  failed: number;
  running: number;
  pending: number;
  progress: number;
  updated_at?: string | null;
  metrics: Record<string, number>;
}

export interface RagPipeline {
  id: string;
  name: string;
  description: string;
  runner: RunnerStatus;
  result: BenchmarkResult;
}
