/**
 * useSupervisorState
 * Polls mcp-server-plughub supervisor_state tool via the REST API proxy.
 * Fires once when sessionId is available, then re-fires on every new WS event
 * (so state is always fresh after each customer turn).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { SupervisorState, WsServerEvent } from "../types";
import { getAccessToken } from "../../../auth/token-store";

const API_BASE = "/api";

export function useSupervisorState(
  sessionId: string | null,
  lastEvent: WsServerEvent | null
): { state: SupervisorState | null; refresh: () => void } {
  const [state, setState] = useState<SupervisorState | null>(null);
  const fetchingRef = useRef(false);

  const fetchState = useCallback(async () => {
    if (!sessionId || fetchingRef.current) return;
    fetchingRef.current = true;
    try {
      // The mcp-server /api/supervisor_state route requires a JWT (requireJwtRole).
      // Without the Bearer token it returns 401 → customer_context (and thus the
      // resolved caller.customer_id / context_snapshot) never reaches the console,
      // which surfaced as "customer not identified" even with the tag in ContextStore.
      const token = getAccessToken();
      const res = await fetch(`${API_BASE}/supervisor_state/${sessionId}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.ok) {
        const data = (await res.json()) as SupervisorState;
        setState(data);
      }
    } catch {
      // ignore transient errors — stale state is acceptable
    } finally {
      fetchingRef.current = false;
    }
  }, [sessionId]);

  // Initial fetch + short retries when sessionId arrives.
  // R0 form-fill (wrap-up/aprovação): um contato reivindicado é uma workflow
  // SUSPENSA, sem mensagens de cliente — logo NÃO há evento de WS
  // (message.text/menu.render) que dispare o re-fetch abaixo. Se o primeiro fetch
  // corre com o claim (context_snapshot ainda não legível / transitório), o Console
  // fica no chat "para sempre" (sem re-fetch), em vez de renderizar o DialogForm.
  // Alguns re-fetches curtos garantem que o snapshot (dialog_form_id + resume_token)
  // seja pego logo após o claim. Barato e idempotente (fetchingRef evita concorrência).
  useEffect(() => {
    if (!sessionId) return;
    fetchState();
    const t1 = setTimeout(fetchState, 700);
    const t2 = setTimeout(fetchState, 1800);
    const t3 = setTimeout(fetchState, 3500);
    return () => { clearTimeout(t1); clearTimeout(t2); clearTimeout(t3); };
  }, [fetchState, sessionId]);

  // Re-fetch on events that signal new content or updated AI analysis.
  useEffect(() => {
    if (!lastEvent) return;
    if (
      lastEvent.type === "message.text" ||
      lastEvent.type === "menu.render" ||
      lastEvent.type === "supervisor_state.updated"
    ) {
      fetchState();
    }
  }, [lastEvent, fetchState]);

  return { state, refresh: fetchState };
}
