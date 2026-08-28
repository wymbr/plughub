/**
 * useSessionTrace (Console — follow-up a: journey do workflow nas abas)
 * Fetches the provenance trace (T6) around a session so the History tab can show
 * the PROCESS/journey this contact belongs to (its related sessions), for workflow
 * contacts that have no customer_id but ARE part of a journey (root_session_id).
 *
 * Endpoint: GET /reports/sessions/{session_id}/trace (analytics-api). Fail-soft.
 */
import { useEffect, useState } from "react";
import { apiFetch } from "@/api/apiFetch";

const REPORTS_BASE = "/reports";

export interface TraceNode {
  session_id:        string;
  origin_session_id: string | null;
  spawn_reason:      string | null;
  root_session_id:   string | null;
  channel:           string | null;
  pool_id:           string | null;
  status:            string | null;
  outcome:           string | null;
  opened_at:         string | null;
  closed_at:         string | null;
  depth:             number;          // <0 ancestral, 0 foco, >0 descendente
  journey_boundary?: boolean;
  journey_id?:       string | null;
}

export interface SessionTrace {
  focus_session_id: string;
  focus_journey_id: string | null;
  focus:            TraceNode | null;
  nodes:            TraceNode[];
}

interface UseSessionTraceReturn {
  trace:   SessionTrace | null;
  loading: boolean;
  error:   string | null;
}

export function useSessionTrace(sessionId: string | null): UseSessionTraceReturn {
  const [trace,   setTrace]   = useState<SessionTrace | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) { setTrace(null); setLoading(false); setError(null); return; }
    let cancelled = false;
    setLoading(true); setError(null);

    apiFetch(`${REPORTS_BASE}/sessions/${encodeURIComponent(sessionId)}/trace`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { if (!cancelled) { setTrace(d as SessionTrace); setLoading(false); } })
      .catch(e => { if (!cancelled) { setError(String(e)); setTrace(null); setLoading(false); } });

    return () => { cancelled = true; };
  }, [sessionId]);

  return { trace, loading, error };
}
