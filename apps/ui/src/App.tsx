import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./components/AppShell";
import { DashboardPage } from "./pages/DashboardPage";
import { PipelinesPage } from "./pages/PipelinesPage";
import { ResultsPage } from "./pages/ResultsPage";

export function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<PipelinesPage />} />
        <Route path="/results" element={<ResultsPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
