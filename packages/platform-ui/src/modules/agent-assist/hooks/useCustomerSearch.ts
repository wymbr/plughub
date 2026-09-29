/**
 * useCustomerSearch (Customer History H3)
 * Keyword search over a customer's closed contacts.
 *
 * Endpoint: GET /analytics/sessions/customer/{customerId}/search
 *           ?tenant_id=xxx&q=term[&from&to&channel&outcome&limit]
 *
 * Only fetches when `q` is non-empty (trimmed); debounced. Returns one hit per
 * session ({ …, snippet, score }) — snippet is MASKED content only. Graceful
 * degradation: empty array on error/while loading.
 */

import { useEffect, useState } from "react";
import { SearchHit } from "../types";
import { apiFetch } from '@/api/apiFetch'
import { useAuth } from '@/auth/useAuth'

const ANALYTICS_BASE = "/analytics";   // AUT-20 — mesma origem; sem base configurável
// TNT-01 — o tenant é o da SESSÃO (JWT), nunca a env de tenant do build: ela não é definida
// em build nenhum, então toda instância consultava `tenant_demo`. Sem tenant, não se consulta.
const SEARCH_LIMIT   = 30;
const DEBOUNCE_MS    = 350;

export interface SearchFilters {
  from?:    string;   // ISO date (opened_at lower bound)
  to?:      string;   // ISO date (upper bound)
  channel?: string;
  outcome?: string;
}

interface UseCustomerSearchReturn {
  hits:    SearchHit[];
  loading: boolean;
  error:   string | null;
  active:  boolean;   // true when a query term is set (results view is showing)
}

export function useCustomerSearch(
  customerId: string | null,
  query:      string,
  filters:    SearchFilters = {},
): UseCustomerSearchReturn {
  const [hits,    setHits]    = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  const q = query.trim();
  const active = q.length > 0;

  // Flatten filters into stable deps (objects would re-trigger every render).
  const { from, to, channel, outcome } = filters;

  const { tenantId } = useAuth();

  useEffect(() => {
    if (!tenantId) { setError("no_tenant"); return; }
    if (!customerId || !active) {
      setHits([]);
      setLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    const timer = setTimeout(() => {
      const params = new URLSearchParams({
        tenant_id: tenantId,
        q,
        limit: String(SEARCH_LIMIT),
      });
      if (from)    params.set("from", from);
      if (to)      params.set("to", to);
      if (channel) params.set("channel", channel);
      if (outcome) params.set("outcome", outcome);

      const url =
        `${ANALYTICS_BASE}/sessions/customer/${encodeURIComponent(customerId)}/search` +
        `?${params.toString()}`;

      apiFetch(url)
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json() as Promise<SearchHit[]>;
        })
        .then((data) => {
          if (!cancelled) {
            setHits(Array.isArray(data) ? data : []);
            setLoading(false);
          }
        })
        .catch((err: unknown) => {
          if (!cancelled) {
            setError(err instanceof Error ? err.message : "search failed");
            setHits([]);
            setLoading(false);
          }
        });
    }, DEBOUNCE_MS);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [customerId, q, active, from, to, channel, outcome, tenantId]);

  return { hits, loading, error, active };
}
