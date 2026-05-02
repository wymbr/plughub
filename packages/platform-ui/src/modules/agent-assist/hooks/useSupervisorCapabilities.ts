/**
 * useSupervisorCapabilities
 * Polls mcp-server-plughub supervisor_capabilities tool via the REST API proxy.
 * Re-fires when intent changes OR every 5 turns — whichever comes first.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { SupervisorCapabilities, SupervisorState } from "../types";

const API_BASE = "/api";
const TURN_INTERVAL = 5;

export function useSupervisorCapabilities(
  sessionId: string | null,
  supervisorState: SupervisorState | null
): SupervisorCapabilities | null {
  const [capabilities, setCapabilities] = useState<SupervisorCapabilities | null>(null);
  const lastIntentRef = useRef<string | null>(null);
  const lastTurnRef = useRef<number>(0);
  const fetchingRef = useRef(false);

  const fetchCapabilities = useCallback(async () => {
    if (!sessionId || fetchingRef.current) return;
    fetchingRef.current = true;
    try {
      const res = await fetch(`${API_BASE}/supervisor_capabilities/${sessionId}`);
      if (res.ok) {
        const data = (await res.json()) as SupervisorCapabilities;
        setCapabilities(data);
      }
    } catch {
      // ignore transient errors
    } finally {
      fetchingRef.current = false;
    }
  }, [sessionId]);

  useEffect(() => {
    if (!supervisorState) return;

    const currentIntent = supervisorState.intent.current;
    const currentTurn = supervisorState.turn_count;

    const intentChanged = currentIntent !== lastIntentRef.current;
    const turnIntervalReached =
      currentTurn - lastTurnRef.current >= TURN_INTERVAL;

    if (intentChanged || turnIntervalReached) {
      lastIntentRef.current = currentIntent;
      lastTurnRef.current = currentTurn;
      fetchCapabilities();
    }
  }, [supervisorState, fetchCapabilities]);

  // Initial fetch on mount
  useEffect(() => {
    fetchCapabilities();
  }, [fetchCapabilities]);

  return capabilities;
}
