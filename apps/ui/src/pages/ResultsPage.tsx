import { ResultsIcon } from "../components/Icons";
import { StatusBadge } from "../components/StatusBadge";
import { useRags } from "../hooks/useRags";
import { resultsDownloadUrl } from "../services/api";
import type { RagPipeline, RunnerState } from "../types";

const METRICS = [
  ["faithfulness", "Fidelidade"],
  ["answer_relevancy", "Relevância"],
  ["context_precision", "Precisão do contexto"],
  ["context_recall", "Recall do contexto"],
] as const;

function displayMetric(item: RagPipeline, key: string) {
  const value = item.result.metrics[key];
  return value === undefined ? "—" : value.toFixed(3);
}

function resultState(item: RagPipeline): RunnerState {
  if (!item.runner.available) return "offline";
  if (["running", "queued"].includes(item.runner.state)) return item.runner.state;
  if (item.result.success === item.result.total) return "succeeded";
  if (item.result.failed > 0) return "failed";
  return "idle";
}

export function ResultsPage() {
  const { items, loading, error } = useRags(4000);
  const completed = items.filter((item) => item.result.success === item.result.total).length;
  const processed = items.reduce((total, item) => total + item.result.success, 0);
  const failures = items.reduce((total, item) => total + item.result.failed, 0);

  return (
    <div className="page-container">
      <header className="page-header">
        <div>
          <p className="eyebrow">Evaluation report</p>
          <h1>Resultados</h1>
          <p>Compare o progresso e as médias RAGAS dos seis pipelines.</p>
        </div>
      </header>

      <section className="result-kpis">
        <div><ResultsIcon /><span><strong>{completed}/6</strong>RAGs concluídos</span></div>
        <div><span><strong>{processed}</strong>Perguntas avaliadas</span></div>
        <div><span><strong>{failures}</strong>Falhas a retomar</span></div>
      </section>

      {error && <div className="alert">{error}</div>}

      <section className="results-panel">
        <div className="section-heading compact">
          <div><h2>Comparativo</h2><p>Médias calculadas somente sobre perguntas concluídas.</p></div>
        </div>
        {loading && items.length === 0 ? (
          <div className="empty-state">Carregando resultados...</div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Pipeline</th>
                  <th>Status</th>
                  <th>Progresso</th>
                  {METRICS.map(([, label]) => <th key={label}>{label}</th>)}
                  <th>Arquivo</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id}>
                    <td><strong>{item.name}</strong><small>{item.id}</small></td>
                    <td><StatusBadge state={resultState(item)} /></td>
                    <td><strong>{item.result.progress.toFixed(1)}%</strong><small>{item.result.success}/{item.result.total}</small></td>
                    {METRICS.map(([key]) => <td className="metric-value" key={key}>{displayMetric(item, key)}</td>)}
                    <td>
                      {item.result.success > 0 ? (
                        <a className="download-link" href={resultsDownloadUrl(item.id)}>CSV</a>
                      ) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
