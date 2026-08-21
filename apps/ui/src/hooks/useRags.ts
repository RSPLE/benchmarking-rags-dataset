import { useCallback, useEffect, useRef, useState } from "react";

import { EMPTY_PIPELINES } from "../catalog";
import { fetchRags } from "../services/api";
import type { RagPipeline } from "../types";

export function useRags(refreshInterval = 2500) {
  const [items, setItems] = useState<RagPipeline[]>(EMPTY_PIPELINES);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  const refresh = useCallback(async (silent = false) => {
    const currentRequest = ++requestId.current;
    if (!silent) setLoading(true);
    try {
      const nextItems = await fetchRags();
      if (currentRequest === requestId.current) {
        setItems(nextItems);
        setError(null);
      }
    } catch (reason) {
      if (currentRequest === requestId.current) {
        setError(reason instanceof Error ? reason.message : "Não foi possível carregar os RAGs.");
      }
    } finally {
      if (!silent && currentRequest === requestId.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(true), refreshInterval);
    return () => window.clearInterval(timer);
  }, [refresh, refreshInterval]);

  return { items, loading, error, refresh };
}
