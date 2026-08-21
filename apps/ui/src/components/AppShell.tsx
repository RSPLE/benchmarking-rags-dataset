import type { PropsWithChildren } from "react";
import { NavLink } from "react-router-dom";

import { PipelineIcon, ResultsIcon } from "./Icons";

export function AppShell({ children }: PropsWithChildren) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <NavLink className="brand" to="/" aria-label="RAG Benchmark — início">
          <div className="brand-mark" aria-hidden="true"><span>RB</span></div>
          <div><strong>RAG Benchmark</strong><small>Evaluation workspace</small></div>
        </NavLink>
        <p className="nav-label">Navegação</p>
        <nav className="nav-list" aria-label="Navegação principal">
          <NavLink to="/" end><PipelineIcon />Pipelines</NavLink>
          <NavLink to="/results"><ResultsIcon />Resultados</NavLink>
        </nav>
        <div className="environment-state">
          <span />
          <div><strong>Ambiente Docker</strong><small>6 runners isolados</small></div>
        </div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  );
}
