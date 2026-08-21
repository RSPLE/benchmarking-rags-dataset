import { useCallback, useEffect, useState } from "react";

import { fetchRags } from "../services/api";
import type { RagPipeline } from "../types";

export function useRags(refreshInterval = 2500) {
  const [items, setItems] = useState<RagPipeline[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      setItems(await fetchRags());
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Não foi possível carregar os RAGs.");
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(true), refreshInterval);
    return () => window.clearInterval(timer);
  }, [refresh, refreshInterval]);

  return { items, loading, error, refresh };
}
