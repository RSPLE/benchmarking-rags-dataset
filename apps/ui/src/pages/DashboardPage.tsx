import { useMemo } from "react";

import { useRags } from "../hooks/useRags";
import type { RagPipeline } from "../types";

const METRICS = [
  { key: "faithfulness", label: "Fidedignidade", short: "Fidelidade", color: "#4c72b0" },
  { key: "answer_relevancy", label: "Relevância da resposta", short: "Relevância", color: "#55a868" },
  { key: "context_precision", label: "Precisão do contexto", short: "Precisão", color: "#c44e52" },
  { key: "context_recall", label: "Revocação do contexto", short: "Recall", color: "#8172b2" },
] as const;

function metricMeans(items: RagPipeline[]) {
  return METRICS.map((metric) => {
    const values = items
      .map((item) => item.result.metrics[metric.key])
      .filter((value): value is number => Number.isFinite(value));
    return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
  });
}

function ChartEmpty() {
  return <div className="chart-empty">Os dados aparecerão após a primeira pergunta concluída.</div>;
}

function MetricsBarChart({ values }: { values: number[] }) {
  const hasData = values.some((value) => value > 0);
  return (
    <div className="chart-frame">
      <svg className="chart-svg" viewBox="0 0 620 300" role="img" aria-labelledby="bar-title bar-description">
        <title id="bar-title">Média das métricas RAGAS</title>
        <desc id="bar-description">Gráfico de barras de zero a um para as quatro métricas RAGAS.</desc>
        {[0, .25, .5, .75, 1].map((tick) => {
          const y = 238 - tick * 190;
          return <g key={tick}><line x1="58" x2="598" y1={y} y2={y} className="chart-grid-line" /><text x="47" y={y + 4} textAnchor="end" className="chart-axis-label">{tick.toFixed(2)}</text></g>;
        })}
        {METRICS.map((metric, index) => {
          const value = values[index];
          const height = value * 190;
          const x = 82 + index * 132;
          return (
            <g key={metric.key}>
              <rect x={x} y={238 - height} width="72" height={height} rx="3" fill={metric.color} />
              <text x={x + 36} y={Math.max(228 - height, 36)} textAnchor="middle" className="chart-value">{value.toFixed(3)}</text>
              <text x={x + 36} y="264" textAnchor="middle" className="chart-axis-label">{metric.short}</text>
            </g>
          );
        })}
      </svg>
      {!hasData && <ChartEmpty />}
    </div>
  );
}

function RadarChart({ values }: { values: number[] }) {
  const centerX = 300;
  const centerY = 150;
  const radius = 95;
  const point = (index: number, scale: number) => {
    const angle = -Math.PI / 2 + index * (Math.PI * 2 / METRICS.length);
    return [centerX + Math.cos(angle) * radius * scale, centerY + Math.sin(angle) * radius * scale];
  };
  const polygon = (scale: number) => METRICS.map((_, index) => point(index, scale).join(",")).join(" ");
  const valuePolygon = values.map((value, index) => point(index, value).join(",")).join(" ");
  const hasData = values.some((value) => value > 0);
  return (
    <div className="chart-frame">
      <svg className="chart-svg" viewBox="0 0 600 300" role="img" aria-labelledby="radar-title radar-description">
        <title id="radar-title">Radar de métricas RAGAS</title>
        <desc id="radar-description">Comparação radial entre as médias das quatro métricas.</desc>
        {[.25, .5, .75, 1].map((scale) => <polygon key={scale} points={polygon(scale)} className="radar-grid" />)}
        {METRICS.map((metric, index) => {
          const [x, y] = point(index, 1);
          const [labelX, labelY] = point(index, 1.28);
          return <g key={metric.key}><line x1={centerX} y1={centerY} x2={x} y2={y} className="chart-grid-line" /><text x={labelX} y={labelY + 4} textAnchor="middle" className="chart-axis-label">{metric.short}</text></g>;
        })}
        <polygon points={valuePolygon} className="radar-value" />
        {values.map((value, index) => { const [x, y] = point(index, value); return <circle key={METRICS[index].key} cx={x} cy={y} r="4" className="radar-point" />; })}
      </svg>
      {!hasData && <ChartEmpty />}
    </div>
  );
}

function Heatmap({ items }: { items: RagPipeline[] }) {
  return (
    <div className="heatmap-scroll">
      <div className="heatmap" role="table" aria-label="Heatmap de médias RAGAS por pipeline">
        <div className="heatmap-corner" role="columnheader">Pipeline</div>
        {METRICS.map((metric) => <div className="heatmap-header" role="columnheader" key={metric.key}>{metric.short}</div>)}
        {items.map((item) => (
          <div className="heatmap-row" role="row" key={item.id}>
            <div className="heatmap-name" role="rowheader">{item.name}</div>
            {METRICS.map((metric) => {
              const value = item.result.metrics[metric.key];
              const intensity = Number.isFinite(value) ? .1 + value * .48 : .04;
              return <div className="heatmap-cell" role="cell" key={metric.key} style={{ backgroundColor: `rgba(76, 114, 176, ${intensity})` }}>{Number.isFinite(value) ? value.toFixed(2) : "—"}</div>;
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

function EfficiencyScatter({ items }: { items: RagPipeline[] }) {
  const points = items.flatMap((item) => {
    const tokens = item.result.metrics.answer_total_tokens;
    if (!item.result.success || !Number.isFinite(tokens)) return [];
    return [{ name: item.name, latency: (item.runner.total_duration_seconds ?? 0) / item.result.success, tokens }];
  });
  const maxLatency = Math.max(...points.map((point) => point.latency), 1);
  const maxTokens = Math.max(...points.map((point) => point.tokens), 1);
  return (
    <div className="chart-frame">
      <svg className="chart-svg" viewBox="0 0 620 300" role="img" aria-labelledby="scatter-title scatter-description">
        <title id="scatter-title">Consumo de tokens por latência</title>
        <desc id="scatter-description">Dispersão dos pipelines; o canto inferior esquerdo representa maior eficiência.</desc>
        {[0, .25, .5, .75, 1].map((tick) => <g key={tick}><line x1="62" x2="594" y1={238 - tick * 188} y2={238 - tick * 188} className="chart-grid-line" /><text x="51" y={242 - tick * 188} textAnchor="end" className="chart-axis-label">{Math.round(maxTokens * tick)}</text></g>)}
        <line x1="62" x2="594" y1="238" y2="238" className="chart-axis" />
        <line x1="62" x2="62" y1="48" y2="238" className="chart-axis" />
        {points.map((point) => {
          const x = 62 + (point.latency / maxLatency) * 500;
          const y = 238 - (point.tokens / maxTokens) * 188;
          return <g key={point.name}><circle cx={x} cy={y} r="8" className="scatter-point" /><text x={x + 11} y={y + 4} className="chart-value">{point.name}</text></g>;
        })}
        <text x="328" y="282" textAnchor="middle" className="chart-axis-label">Latência média por pergunta (segundos)</text>
      </svg>
      {!points.length && <ChartEmpty />}
    </div>
  );
}

export function DashboardPage() {
  const { items, error } = useRags(4000);
  const means = useMemo(() => metricMeans(items), [items]);
  const evaluated = items.reduce((sum, item) => sum + item.result.success, 0);
  return (
    <div className="page-container dashboard-page">
      <header className="page-header"><div><p className="eyebrow">Visão analítica</p><h1>Dashboard</h1><p>Os mesmos modelos de gráfico usados pelos RAGs, alimentados pelos resultados persistidos do benchmark.</p></div></header>
      {error && <div className="alert">{error}</div>}
      <div className="dashboard-note"><strong>{evaluated}</strong><span>perguntas concluídas alimentando os gráficos</span></div>
      <div className="dashboard-grid">
        <section className="chart-panel"><header><h2>Métricas médias</h2><p>Escala RAGAS de 0 a 1.</p></header><MetricsBarChart values={means} /></section>
        <section className="chart-panel"><header><h2>Radar de métricas</h2><p>Equilíbrio entre as quatro dimensões.</p></header><RadarChart values={means} /></section>
        <section className="chart-panel chart-panel-wide"><header><h2>Heatmap de scores</h2><p>Média de cada métrica por pipeline.</p></header><Heatmap items={items} /></section>
        <section className="chart-panel chart-panel-wide"><header><h2>Tokens × latência</h2><p>Como no benchmark original: quanto mais próximo do canto inferior esquerdo, mais eficiente.</p></header><EfficiencyScatter items={items} /></section>
      </div>
    </div>
  );
}
