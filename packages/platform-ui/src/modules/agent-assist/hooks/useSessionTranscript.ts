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
import { apiFetch } from '@/api/apiFetch'
import { useAuth } from '@/auth/useAuth'

const ANALYTICS_BASE = "/analytics";   // AUT-20 — mesma origem; sem base configurável
// TNT-01 — o tenant é o da SESSÃO (JWT), nunca a env de tenant do build: ela não é definida
// em build nenhum, então toda instância consultava `tenant_demo`. Sem tenant, não se consulta.

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

  const { tenantId } = useAuth();

  useEffect(() => {
    if (!tenantId) { setError("no_tenant"); return; }
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
      `?tenant_id=${encodeURIComponent(tenantId)}&scope=contact`;

    apiFetch(url)
      .then(async (res) => {
        // MOD-07: desde o corte #4 esta rota exige `contacts.transcricao` (o diálogo
        // é campo próprio; `visualizar` ficou com as listas). O servidor devolve
        // `capability_denied: contacts.<campo>` — mostrar só "HTTP 403" mandaria o
        // operador adivinhar qual grant lhe falta.
        if (!res.ok) {
          const detalhe = await res.json().then(
            (b: { detail?: string }) => b?.detail, () => undefined)
          throw new Error(detalhe ? `HTTP ${res.status} — ${detalhe}` : `HTTP ${res.status}`);
        }
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
  }, [sessionId, tenantId]);

  return { messages, loading, error };
}
