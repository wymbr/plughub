/**
 * useCustomer360 (Cliente 360 — C1b: agregado de qualidade + survey + contatos)
 * Fetches the aggregated 360 view for an identified customer so the Cliente tab can
 * show quality (evaluation_finalized, Oficial), voice-of-customer (session_signal)
 * and a contacts summary — all keyed by customer_id.
 *
 * Endpoint: GET /reports/customers/{customer_id}/360?tenant_id (analytics-api, ADR §D4).
 * Same /reports proxy the Vista Processos / journeys use. Fail-soft.
 *
 * ⚠️ "Optional auth" (o que esta linha dizia até 2026-08-29) descrevia um contrato que
 * era um DEFEITO, não uma escolha: a rota não declarava principal algum e respondia
 * 200 anônimo — medido — servindo o 360 de qualquer cliente. Ela passou a exigir
 * credencial, e por isso a chamada aqui virou `apiFetch`: com `fetch` cru a aba
 * Cliente ficaria VAZIA, e o `catch` abaixo transformaria o 401 em "este cliente não
 * tem dado", que é indistinguível de resposta.
 */

import { useEffect, useState } from "react";
import { apiFetch } from "@/api/apiFetch";

const REPORTS_BASE = "/reports";
const TENANT_ID    = import.meta.env.VITE_TENANT_ID ?? "tenant_demo";

export interface Customer360Contacts {
  total:            number;
  resolved:         number;
  open_count:       number;
  channels:         string[];
  last_contact_at:  string | null;
}

export interface Customer360Quality {
  count:        number;
  avg_score:    number | null;
  min_score:    number | null;
  max_score:    number | null;
  latest_score: number | null;
  latest_at:    string | null;
}

export interface Customer360Survey {
  metric:       string;
  count:        number;
  avg_value:    number | null;
  latest_value: number | null;
  latest_label: string | null;
  latest_at:    string | null;
}

export interface Customer360 {
  customer_id: string;
  contacts:    Customer360Contacts | null;
  quality:     Customer360Quality | null;
  surveys:     Customer360Survey[];
}

interface UseCustomer360Return {
  data:    Customer360 | null;
  loading: boolean;
  error:   string | null;
  refetch: () => void;
}

export function useCustomer360(customerId: string | null): UseCustomer360Return {
  const [data,     setData]     = useState<Customer360 | null>(null);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState<string | null>(null);
  const [fetchKey, setFetchKey] = useState(0);

  const refetch = () => setFetchKey(k => k + 1);

  useEffect(() => {
    if (!customerId) { setData(null); setLoading(false); setError(null); return; }
    let cancelled = false;
    setLoading(true); setError(null);

    const url = `${REPORTS_BASE}/customers/${encodeURIComponent(customerId)}/360?${new URLSearchParams({
      tenant_id: TENANT_ID,
    })}`;

    apiFetch(url)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { if (!cancelled) { setData(d as Customer360); setLoading(false); } })
      .catch(e => { if (!cancelled) { setError(String(e)); setData(null); setLoading(false); } });

    return () => { cancelled = true; };
  }, [customerId, fetchKey]);

  return { data, loading, error, refetch };
}
