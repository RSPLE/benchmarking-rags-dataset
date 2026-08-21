import { useMemo, useState } from "react";

import { PlayIcon, RefreshIcon } from "../components/Icons";
import { StatusBadge } from "../components/StatusBadge";
import { useRags } from "../hooks/useRags";
import { cancelRag, startRag } from "../services/api";
import type { RagPipeline } from "../types";

function effectiveState(item: RagPipeline) {
  if (!item.runner.available) return "offline" as const;
  if (item.runner.state === "idle" && item.result.failed > 0) return "failed" as const;
  if (item.runner.state === "idle" && item.result.success === item.result.total) {
    return "succeeded" as const;
  }
  return item.runner.state;
}

function actionLabel(item: RagPipeline) {
  if (item.result.failed > 0) return "Retomar falhas";
  if (item.result.success > 0) return "Continuar";
  return "Executar teste";
}

export function PipelinesPage() {
  const { items, loading, error, refresh } = useRags();
  const [selected, setSelected] = useState<RagPipeline | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const activeCount = useMemo(
    () => items.filter((item) => ["queued", "running"].includes(item.runner.state)).length,
    [items],
  );
  const completedCount = useMemo(
    () => items.filter((item) => item.result.success === item.result.total).length,
    [items],
  );

  async function execute(item: RagPipeline) {
    setPendingAction(item.id);
    setActionError(null);
    try {
      await startRag(item.id);
      await refresh(true);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Falha ao iniciar pipeline.");
    } finally {
      setPendingAction(null);
    }
  }

  async function cancel(item: RagPipeline) {
    setPendingAction(item.id);
    try {
      await cancelRag(item.id);
      await refresh(true);
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Falha ao cancelar pipeline.");
    } finally {
      setPendingAction(null);
    }
  }

  return (
    <div className="page-container">
      <header className="page-header">
        <div>
          <p className="eyebrow">Benchmark workspace</p>
          <h1>Pipelines RAG</h1>
          <p>Execute, acompanhe e retome cada estratégia de avaliação isoladamente.</p>
        </div>
        <button className="button button-secondary" onClick={() => void refresh()}>
          <RefreshIcon /> Atualizar
        </button>
      </header>

      <section className="summary-strip" aria-label="Resumo dos pipelines">
        <div><strong>6</strong><span>Pipelines</span></div>
        <div><strong>{activeCount}</strong><span>Em execução</span></div>
        <div><strong>{completedCount}</strong><span>Concluídos</span></div>
        <div><strong>90</strong><span>Perguntas por RAG</span></div>
      </section>

      {(error || actionError) && <div className="alert">{actionError ?? error}</div>}

      <section className="section-heading">
        <div>
          <h2>Jobs disponíveis</h2>
          <p>Cada job usa seu próprio ambiente uv dentro de um container.</p>
        </div>
      </section>

      {loading && items.length === 0 ? (
        <div className="empty-state">Carregando runners...</div>
      ) : (
        <div className="pipeline-grid">
          {items.map((item, index) => {
            const state = effectiveState(item);
            const isActive = ["queued", "running"].includes(item.runner.state);
            const isComplete = item.result.success === item.result.total;
            return (
              <article className="pipeline-card" key={item.id}>
                <div className="pipeline-card-top">
                  <span className="job-number">JOB {String(index + 1).padStart(2, "0")}</span>
                  <StatusBadge state={state} />
                </div>
                <h3>{item.name}</h3>
                <p className="pipeline-description">{item.description}</p>
                <div className="progress-row">
                  <span>Progresso</span>
                  <strong>{item.result.success}/{item.result.total}</strong>
                </div>
                <div className="progress-track" aria-label={`${item.result.progress}% concluído`}>
                  <span style={{ width: `${item.result.progress}%` }} />
                </div>
                <div className="pipeline-counts">
                  <span><i className="dot-success" />{item.result.success} sucesso</span>
                  <span><i className="dot-failed" />{item.result.failed} falha</span>
                  <span>{item.result.pending} pendente</span>
                </div>
                <div className="pipeline-actions">
                  {isActive ? (
                    <button
                      className="button button-danger"
                      disabled={pendingAction === item.id}
                      onClick={() => void cancel(item)}
                    >
                      Cancelar
                    </button>
                  ) : (
                    <button
                      className="button button-primary"
                      disabled={!item.runner.available || isComplete || pendingAction === item.id}
                      onClick={() => void execute(item)}
                    >
                      <PlayIcon /> {isComplete ? "Concluído" : actionLabel(item)}
                    </button>
                  )}
                  <button className="button button-ghost" onClick={() => setSelected(item)}>
                    Ver logs
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {selected && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setSelected(null)}>
          <section
            className="log-modal"
            role="dialog"
            aria-modal="true"
            aria-label={`Logs de ${selected.name}`}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="modal-header">
              <div><span>Log do job</span><h2>{selected.name}</h2></div>
              <button onClick={() => setSelected(null)} aria-label="Fechar">×</button>
            </div>
            <pre>{selected.runner.logs.length ? selected.runner.logs.join("\n") : "Nenhum log disponível."}</pre>
          </section>
        </div>
      )}
    </div>
  );
}
