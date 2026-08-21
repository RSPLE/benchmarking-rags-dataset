import type { RagPipeline } from "../types";
import { normalizePipelines } from "../catalog";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: "Falha inesperada" }));
    throw new Error(payload.detail ?? `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchRags(signal?: AbortSignal): Promise<RagPipeline[]> {
  const payload = await request<{ items: RagPipeline[] }>("/api/rags", { signal });
  return normalizePipelines(payload.items);
}

export async function startRag(project: string): Promise<void> {
  await request(`/api/rags/${project}/run`, { method: "POST" });
}

export async function cancelRag(project: string): Promise<void> {
  await request(`/api/rags/${project}/cancel`, { method: "POST" });
}

export function resultsDownloadUrl(project: string): string {
  return `${API_BASE}/api/rags/${project}/results.csv`;
}
