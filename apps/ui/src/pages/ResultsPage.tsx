import { ResultsIcon } from "../components/Icons";
import { StatusBadge } from "../components/StatusBadge";
import { formatDuration, totalDuration } from "../format";
import { useRags } from "../hooks/useRags";
import { resultsDownloadUrl } from "../services/api";
import type { RagPipeline } from "../types";

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

export function ResultsPage() {
  const { items, loading, error } = useRags(4000);
  const completed = items.filter((item) => item.result.success === item.result.total).length;
  const processed = items.reduce((total, item) => total + item.result.success, 0);
  const failures = items.reduce((total, item) => total + item.result.failed, 0);

  return (
    <div className="page-container results-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Relatório de avaliação</p>
          <h1>Resultados</h1>
          <p>Progresso, tempo acumulado e médias RAGAS dos seis pipelines.</p>
        </div>
      </header>

      <section className="result-kpis">
        <div><ResultsIcon /><span><small>RAGs concluídos</small><strong>{completed}<em>/6</em></strong></span></div>
        <div><span><small>Perguntas avaliadas</small><strong>{processed}</strong></span></div>
        <div><span><small>Falhas a retomar</small><strong>{failures}</strong></span></div>
      </section>

      {error && <div className="alert">{error}</div>}

      <section className="results-panel">
        <div className="section-heading compact">
          <div><h2>Comparativo</h2><p>Médias calculadas somente sobre perguntas concluídas.</p></div>
        </div>
        <div className={`table-scroll ${loading ? "is-loading" : ""}`} aria-busy={loading}>
          <table>
            <thead>
              <tr>
                <th>Pipeline</th><th>Status</th><th>Progresso</th><th>Tempo total</th>
                {METRICS.map(([, label]) => <th key={label}>{label}</th>)}
                <th>Arquivo</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td><strong>{item.name}</strong><small>{item.id}</small></td>
                  <td><StatusBadge state={item.display_state} /><small className="status-explanation">{item.state_reason}</small></td>
                  <td><strong>{item.result.progress.toFixed(1)}%</strong><small>{item.result.success}/{item.result.total}</small></td>
                  <td className="duration-cell">{formatDuration(totalDuration(item))}</td>
                  {METRICS.map(([key]) => <td className="metric-value" key={key}>{displayMetric(item, key)}</td>)}
                  <td>{item.result.success > 0 ? <a className="download-link" href={resultsDownloadUrl(item.id)}>CSV</a> : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
