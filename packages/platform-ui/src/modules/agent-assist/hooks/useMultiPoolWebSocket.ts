/**
 * useMultiPoolWebSocket
 * Manages one persistent WebSocket per active pool.
 *
 * Design:
 *   - `activePools` drives the set of open connections.
 *     Adding a pool_id opens a new WS; removing one closes it.
 *   - Each connection sends the same typed envelope as useAgentWebSocket.
 *   - `lastEvent` is the most recent event from ANY pool, tagged with `_pool_id`.
 *   - `send(text, sessionId)` targets the correct connection via session→pool
 *     mapping (registered on conversation.assigned). Falls back to broadcast
 *     if the mapping is missing. Each server-side connection validates
 *     subscribedSessions independently.
 *   - `statuses` is a Map<poolId, PoolConnectionStatus> for the sidebar dots.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { WsServerEvent } from "../types";

const WS_BASE = import.meta.env.VITE_MCP_WS_URL ?? "/agent-ws";
const RECONNECT_DELAY_MS = 3_000;
const HEARTBEAT_INTERVAL_MS = 30_000;
const DISCONNECT_DEBOUNCE_MS = 2_000;

export type PoolConnectionStatus = "connecting" | "connected" | "disconnected";

/** A WsServerEvent tagged with the source pool_id. */
export type TaggedWsEvent = WsServerEvent & { _pool_id: string };

interface PoolState {
  ws: WebSocket;
  reconnectTimer?: ReturnType<typeof setTimeout>;
  heartbeatTimer?: ReturnType<typeof setInterval>;
  disconnectDebounce?: ReturnType<typeof setTimeout>;
  intentionalClose: boolean;
}

/**
 * Maps sessionId → poolId so send() can target the correct WebSocket
 * connection. Populated externally via registerSession() when
 * conversation.assigned arrives.
 */
type SessionPoolMap = Map<string, string>;

interface UseMultiPoolWebSocketReturn {
  /** Current connection status per pool. */
  statuses:  Map<string, PoolConnectionStatus>;
  /** Most recent event from any pool (includes _pool_id). */
  lastEvent: TaggedWsEvent | null;
  /** Send a text message targeting a specific session. */
  send:      (text: string, sessionId: string) => void;
  /**
   * Register which pool owns a session. Call when conversation.assigned
   * arrives so send() can target the correct WebSocket connection.
   */
  registerSession: (sessionId: string, poolId: string) => void;
  /** Unregister a session (e.g. on session.closed). */
  unregisterSession: (sessionId: string) => void;
}

function openConnection(
  poolId:       string,
  userId:       string,
  maxConcurrent: number,
  poolStateRef: React.MutableRefObject<Map<string, PoolState>>,
  setStatuses: React.Dispatch<React.SetStateAction<Map<string, PoolConnectionStatus>>>,
  setLastEvent: React.Dispatch<React.SetStateAction<TaggedWsEvent | null>>,
) {
  const params = new URLSearchParams();
  params.set("pool", poolId);
  if (userId) params.set("user_id", userId);
  params.set("max_concurrent", String(maxConcurrent));
  const url = `${WS_BASE}?${params.toString()}`;

  const ws = new WebSocket(url);

  const state: PoolState = {
    ws,
    intentionalClose: false,
  };
  poolStateRef.current.set(poolId, state);

  setStatuses(prev => new Map(prev).set(poolId, "connecting"));

  ws.onopen = () => {
    const s = poolStateRef.current.get(poolId);
    if (!s) return;
    if (s.disconnectDebounce) {
      clearTimeout(s.disconnectDebounce);
      s.disconnectDebounce = undefined;
    }
    setStatuses(prev => new Map(prev).set(poolId, "connected"));

    // Heartbeat
    s.heartbeatTimer = setInterval(() => {
      if (s.ws.readyState === WebSocket.OPEN) {
        s.ws.send(JSON.stringify({ type: "pong" }));
      }
    }, HEARTBEAT_INTERVAL_MS);
  };

  ws.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data) as WsServerEvent;
      setLastEvent({ ...data, _pool_id: poolId } as TaggedWsEvent);
    } catch {
      // ignore malformed messages
    }
  };

  ws.onerror = () => {
    // handled by onclose
  };

  ws.onclose = () => {
    const s = poolStateRef.current.get(poolId);
    if (!s) return;

    if (s.heartbeatTimer) {
      clearInterval(s.heartbeatTimer);
      s.heartbeatTimer = undefined;
    }

    if (!s.intentionalClose) {
      // Debounce the disconnected status to hide brief reconnects
      s.disconnectDebounce = setTimeout(() => {
        setStatuses(prev => new Map(prev).set(poolId, "disconnected"));
      }, DISCONNECT_DEBOUNCE_MS);

      // Schedule reconnect — preserve userId/maxConcurrent captured in closure
      s.reconnectTimer = setTimeout(() => {
        const current = poolStateRef.current.get(poolId);
        if (current && !current.intentionalClose) {
          openConnection(poolId, userId, maxConcurrent, poolStateRef, setStatuses, setLastEvent);
        }
      }, RECONNECT_DELAY_MS);
    } else {
      setStatuses(prev => {
        const next = new Map(prev);
        next.delete(poolId);
        return next;
      });
      poolStateRef.current.delete(poolId);
    }
  };
}

function closeConnection(poolId: string, poolStateRef: React.MutableRefObject<Map<string, PoolState>>) {
  const s = poolStateRef.current.get(poolId);
  if (!s) return;
  s.intentionalClose = true;
  if (s.reconnectTimer) clearTimeout(s.reconnectTimer);
  if (s.heartbeatTimer) clearInterval(s.heartbeatTimer);
  if (s.disconnectDebounce) clearTimeout(s.disconnectDebounce);
  s.ws.close();
}

export function useMultiPoolWebSocket(
  activePools:   string[],
  userId?:       string,
  maxConcurrent?: number,
): UseMultiPoolWebSocketReturn {
  const poolStateRef    = useRef<Map<string, PoolState>>(new Map());
  const sessionPoolRef  = useRef<SessionPoolMap>(new Map());
  const [statuses,  setStatuses]  = useState<Map<string, PoolConnectionStatus>>(new Map());
  const [lastEvent, setLastEvent] = useState<TaggedWsEvent | null>(null);

  // Synchronize open connections with activePools
  useEffect(() => {
    const desired = new Set(activePools);
    const current = new Set(poolStateRef.current.keys());

    // Open new connections
    for (const poolId of desired) {
      if (!current.has(poolId)) {
        openConnection(poolId, userId ?? "", maxConcurrent ?? 3, poolStateRef, setStatuses, setLastEvent);
      }
    }

    // Close removed connections
    for (const poolId of current) {
      if (!desired.has(poolId)) {
        closeConnection(poolId, poolStateRef);
      }
    }

    // Cleanup: close all on unmount
    return () => {
      // no-op: handled individually above or on next effect run
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePools.join(",")]);

  // Close everything on unmount
  useEffect(() => {
    return () => {
      for (const poolId of poolStateRef.current.keys()) {
        closeConnection(poolId, poolStateRef);
      }
    };
  }, []);

  const send = useCallback((text: string, sessionId: string, visibility?: string) => {
    if (!sessionId) return;
    const payload: Record<string, unknown> = {
      type:       "message.text",
      session_id: sessionId,
      text,
      timestamp:  new Date().toISOString(),
    };
    if (visibility) payload["visibility"] = visibility;
    const envelope = JSON.stringify(payload);

    // Each server-side WS connection has its own subscribedSessions set.
    // Only the connection that received conversation.assigned for this
    // sessionId will process the message; others silently drop it.
    //
    // Strategy: try the known pool first (if registered), then broadcast
    // to ALL open connections as fallback. The server-side validation
    // ensures only the correct connection processes it.
    const knownPool = sessionPoolRef.current.get(sessionId);
    if (knownPool) {
      const state = poolStateRef.current.get(knownPool);
      if (state && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(envelope);
        return;
      }
    }

    // Fallback: broadcast to all open connections
    for (const { ws } of poolStateRef.current.values()) {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(envelope);
        // Don't return — send to ALL connections so the right one picks it up
      }
    }
  }, []);

  const registerSession = useCallback((sessionId: string, poolId: string) => {
    sessionPoolRef.current.set(sessionId, poolId);
  }, []);

  const unregisterSession = useCallback((sessionId: string) => {
    sessionPoolRef.current.delete(sessionId);
  }, []);

  return { statuses, lastEvent, send, registerSession, unregisterSession };
}
