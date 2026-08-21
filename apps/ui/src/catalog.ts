import type { RagPipeline } from "./types";

const PIPELINES = [
  ["context-rag", "Context RAG", "Busca vetorial clássica com resposta restrita ao contexto."],
  ["graph-rag", "Graph RAG", "Busca vetorial combinada a um grafo NetworkX extraído por LLM."],
  ["hybrid-rag", "Hybrid RAG", "Recuperação híbrida BM25 e vetorial."],
  ["knowledge-enhanced-rag", "Knowledge-Enhanced RAG", "Busca vetorial enriquecida por grafo de conhecimento Neo4j."],
  ["memory-augmented-rag", "Memory-Augmented RAG", "Agente RAG com memória conversacional."],
  ["self-rag", "Self-RAG", "Geração com autocrítica e uma etapa de refinamento."],
] as const;

export const PIPELINE_IDS = PIPELINES.map(([id]) => id);

export function emptyPipeline(id: string, name: string, description: string): RagPipeline {
  return {
    id,
    name,
    description,
    display_state: "offline",
    state_reason: "Aguardando a primeira resposta da API do dashboard.",
    runner: {
      available: false,
      state: "offline",
      duration_seconds: 0,
      total_duration_seconds: 0,
      logs: [],
    },
    result: {
      total: 90,
      success: 0,
      failed: 0,
      running: 0,
      pending: 90,
      progress: 0,
      metrics: {},
    },
  };
}

export const EMPTY_PIPELINES = PIPELINES.map(([id, name, description]) =>
  emptyPipeline(id, name, description),
);

export function normalizePipelines(items: RagPipeline[]): RagPipeline[] {
  const received = new Map(items.map((item) => [item.id, item]));
  return EMPTY_PIPELINES.map((fallback) => received.get(fallback.id) ?? fallback);
}
