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

const ANALYTICS_BASE = import.meta.env.VITE_ANALYTICS_URL ?? "/analytics";
const TENANT_ID      = import.meta.env.VITE_TENANT_ID ?? "tenant_demo";
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

  useEffect(() => {
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
        tenant_id: TENANT_ID,
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
  }, [customerId, q, active, from, to, channel, outcome]);

  return { hits, loading, error, active };
}
