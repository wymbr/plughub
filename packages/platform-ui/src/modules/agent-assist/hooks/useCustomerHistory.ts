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
import { apiFetch } from '@/api/apiFetch'
import { useAuth } from '@/auth/useAuth'

const ANALYTICS_BASE = "/analytics";   // AUT-20 — mesma origem; sem base configurável
// TNT-01 — o tenant é o da SESSÃO (JWT), nunca a env de tenant do build: ela não é definida
// em build nenhum, então toda instância consultava `tenant_demo`. Sem tenant, não se consulta.
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

  const { tenantId } = useAuth();

  useEffect(() => {
    if (!tenantId) { setError("no_tenant"); return; }
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
      `?tenant_id=${encodeURIComponent(tenantId)}&limit=${HISTORY_LIMIT}`;

    apiFetch(url)
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
  }, [customerId, fetchKey, tenantId]);

  return { entries, loading, error, refetch };
}
