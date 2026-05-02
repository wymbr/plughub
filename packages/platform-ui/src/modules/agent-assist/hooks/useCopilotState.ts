/**
 * useCopilotState
 * Fetches co-pilot suggestions from mcp-server-plughub GET /copilot_state/:sessionId.
 *
 * Re-fetches when:
 *  - sessionId changes (new contact)
 *  - A "copilot.updated" WebSocket event arrives (lastCopilotEvent changes)
 *
 * The AI Gateway fires copilot.updated via Redis pub/sub after each customer
 * message is analyzed. The WS hook receives it and passes a bumped timestamp
 * here as lastCopilotEvent so we re-fetch immediately.
 *
 * Fire-and-forget fetches — errors are silently ignored (non-critical UI feature).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { CopilotSuggestions } from "../types";

const API_BASE = "/api";

export function useCopilotState(
  sessionId: string | null,
  lastCopilotEvent: number   // bump this to trigger a re-fetch (e.g. Date.now())
): CopilotSuggestions | null {
  const [suggestions, setSuggestions] = useState<CopilotSuggestions | null>(null);
  const fetchingRef = useRef(false);

  const fetchCopilotState = useCallback(async () => {
    if (!sessionId || fetchingRef.current) return;
    fetchingRef.current = true;
    try {
      const res = await fetch(`${API_BASE}/copilot_state/${sessionId}`);
      if (res.ok) {
        const data = (await res.json()) as CopilotSuggestions & { session_id?: string };
        setSuggestions({
          sugestao_resposta:  data.sugestao_resposta  ?? null,
          flags_risco:        Array.isArray(data.flags_risco)        ? data.flags_risco        : [],
          acoes_recomendadas: Array.isArray(data.acoes_recomendadas) ? data.acoes_recomendadas : [],
          ultima_analise:     data.ultima_analise ?? null,
        });
      }
    } catch {
      // ignore transient network errors — co-pilot is a best-effort feature
    } finally {
      fetchingRef.current = false;
    }
  }, [sessionId]);

  // Initial fetch when session becomes active
  useEffect(() => {
    if (!sessionId) {
      setSuggestions(null);
      return;
    }
    fetchCopilotState();
  }, [sessionId, fetchCopilotState]);

  // Re-fetch when AI Gateway signals analysis is ready (copilot.updated via WS)
  useEffect(() => {
    if (!sessionId || lastCopilotEvent === 0) return;
    fetchCopilotState();
  }, [lastCopilotEvent, sessionId, fetchCopilotState]);

  return suggestions;
}
