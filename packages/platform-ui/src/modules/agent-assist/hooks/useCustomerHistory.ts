/**
 * useCustomerHistory
 * Fetches the closed-session history for a customer from the analytics-api.
 *
 * Endpoint: GET /analytics/sessions/customer/{customerId}?tenant_id=xxx&limit=20
 *
 * Returns an empty array while loading and on error (graceful degradation).
 * Re-fetches automatically whenever customerId changes.
 */

import { useEffect, useState } from "react";
import { ContactHistoryEntry } from "../types";

const ANALYTICS_BASE = import.meta.env.VITE_ANALYTICS_URL ?? "/analytics";
const TENANT_ID      = import.meta.env.VITE_TENANT_ID ?? "tenant_demo";
const HISTORY_LIMIT  = 20;

interface UseCustomerHistoryReturn {
  entries:  ContactHistoryEntry[];
  loading:  boolean;
  error:    string | null;
  refetch:  () => void;
}

export function useCustomerHistory(
  customerId: string | null,
): UseCustomerHistoryReturn {
  const [entries,  setEntries]  = useState<ContactHistoryEntry[]>([]);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState<string | null>(null);
  const [fetchKey, setFetchKey] = useState(0);

  const refetch = () => setFetchKey((k) => k + 1);

  useEffect(() => {
    if (!customerId) {
      setEntries([]);
      setLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    const url =
      `${ANALYTICS_BASE}/sessions/customer/${encodeURIComponent(customerId)}` +
      `?tenant_id=${encodeURIComponent(TENANT_ID)}&limit=${HISTORY_LIMIT}`;

    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<ContactHistoryEntry[]>;
      })
      .then((data) => {
        if (!cancelled) {
          setEntries(Array.isArray(data) ? data : []);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Erro ao carregar histórico");
          setEntries([]);
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [customerId, fetchKey]);

  return { entries, loading, error, refetch };
}
