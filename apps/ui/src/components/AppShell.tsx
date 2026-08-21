import type { PropsWithChildren } from "react";
import { NavLink } from "react-router-dom";

import { PipelineIcon, ResultsIcon } from "./Icons";

export function AppShell({ children }: PropsWithChildren) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">R</div>
          <div>
            <strong>RAG Benchmark</strong>
            <span>Evaluation lab</span>
          </div>
        </div>
        <p className="nav-label">Workspace</p>
        <nav className="nav-list" aria-label="Navegação principal">
          <NavLink to="/" end>
            <PipelineIcon />
            Pipelines
          </NavLink>
          <NavLink to="/results">
            <ResultsIcon />
            Resultados
          </NavLink>
        </nav>
        <div className="sidebar-footer">
          <span className="environment-dot" />
          Docker environment
          <small>6 runners isolados</small>
        </div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  );
}
