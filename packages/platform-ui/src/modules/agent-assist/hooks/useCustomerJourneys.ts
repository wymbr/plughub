/**
 * useCustomerJourneys (Cliente 360 — HJ: jornadas em aberto no Histórico)
 * Fetches the PROCESSES (journeys) the customer participates in, so the History
 * tab can distinguish a process (PRC-…) from an individual contact (session).
 *
 * Endpoint: GET /reports/journeys?tenant_id&customer_id=<id> (analytics-api, ADR §D2).
 * Same proxy the Vista Processos uses. Optional auth (no 401). Graceful degradation.
 */

import { useEffect, useState } from "react";

const REPORTS_BASE = "/reports";
const TENANT_ID    = import.meta.env.VITE_TENANT_ID ?? "tenant_demo";

export interface CustomerJourney {
  journey_id:        string;
  session_count:     number;
  started_at:        string;
  last_activity_at:  string;
  channels:          string[];
  pool_ids:          string[];
  open_count:        number;
  business_outcome?: string | null;
}

interface UseCustomerJourneysReturn {
  journeys: CustomerJourney[];
  loading:  boolean;
  error:    string | null;
  refetch:  () => void;
}

export function useCustomerJourneys(customerId: string | null): UseCustomerJourneysReturn {
  const [journeys, setJourneys] = useState<CustomerJourney[]>([]);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState<string | null>(null);
  const [fetchKey, setFetchKey] = useState(0);

  const refetch = () => setFetchKey(k => k + 1);

  useEffect(() => {
    if (!customerId) { setJourneys([]); setLoading(false); setError(null); return; }
    let cancelled = false;
    setLoading(true); setError(null);

    const url = `${REPORTS_BASE}/journeys?${new URLSearchParams({
      tenant_id: TENANT_ID,
      customer_id: customerId,
      // significant_only=true (default): só PROCESSOS reais (multi-sessão / webhook) —
      // contatos avulsos (1 sessão) NÃO viram "journey" aqui, ficam na lista de contatos.
      page_size: "50",
    })}`;

    fetch(url)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { if (!cancelled) { setJourneys(d.data ?? []); setLoading(false); } })
      .catch(e => { if (!cancelled) { setError(String(e)); setJourneys([]); setLoading(false); } });

    return () => { cancelled = true; };
  }, [customerId, fetchKey]);

  return { journeys, loading, error, refetch };
}
