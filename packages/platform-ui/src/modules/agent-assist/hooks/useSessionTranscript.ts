/**
 * useSessionTranscript
 * Fetches the MASKED message transcript for a single closed session from
 * analytics-api, used by the History tab drill-down (Customer History H1).
 *
 * Endpoint: GET /analytics/v1/transcript/sessions/{sessionId}?tenant_id=xxx&scope=contact
 *   - scope=contact returns the full session (no segment window).
 *   - Content is masked by construction (analytics.messages has no
 *     original_content column) → LGPD-safe, no unmasked exposure, no audit row.
 *
 * Lazy: pass sessionId=null to stay idle (no fetch). Returns empty on error
 * (graceful degradation). Re-fetches whenever sessionId changes.
 */

import { useEffect, useState } from "react";
import { TranscriptMessage } from "../types";

const ANALYTICS_BASE = import.meta.env.VITE_ANALYTICS_URL ?? "/analytics";
const TENANT_ID      = import.meta.env.VITE_TENANT_ID ?? "tenant_demo";

interface UseSessionTranscriptReturn {
  messages: TranscriptMessage[];
  loading:  boolean;
  error:    string | null;
}

export function useSessionTranscript(
  sessionId: string | null,
): UseSessionTranscriptReturn {
  const [messages, setMessages] = useState<TranscriptMessage[]>([]);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setMessages([]);
      setLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    const url =
      `${ANALYTICS_BASE}/v1/transcript/sessions/${encodeURIComponent(sessionId)}` +
      `?tenant_id=${encodeURIComponent(TENANT_ID)}&scope=contact`;

    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<{ messages?: TranscriptMessage[] }>;
      })
      .then((data) => {
        if (!cancelled) {
          setMessages(Array.isArray(data?.messages) ? data.messages : []);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Erro ao carregar transcrição");
          setMessages([]);
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  return { messages, loading, error };
}
