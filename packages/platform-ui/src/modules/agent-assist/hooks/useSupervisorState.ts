/**
 * useSupervisorState
 * Polls mcp-server-plughub supervisor_state tool via the REST API proxy.
 * Fires once when sessionId is available, then re-fires on every new WS event
 * (so state is always fresh after each customer turn).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { SupervisorState, WsServerEvent } from "../types";

const API_BASE = "/api";

export function useSupervisorState(
  sessionId: string | null,
  lastEvent: WsServerEvent | null
): SupervisorState | null {
  const [state, setState] = useState<SupervisorState | null>(null);
  const fetchingRef = useRef(false);

  const fetchState = useCallback(async () => {
    if (!sessionId || fetchingRef.current) return;
    fetchingRef.current = true;
    try {
      const res = await fetch(`${API_BASE}/supervisor_state/${sessionId}`);
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

  // Initial fetch when sessionId arrives
  useEffect(() => {
    fetchState();
  }, [fetchState]);

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

  return state;
}
