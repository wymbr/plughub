/**
 * useMultiPoolWebSocket
 * Manages one persistent WebSocket per active pool.
 *
 * Design:
 *   - `activePools` drives the set of open connections.
 *     Adding a pool_id opens a new WS; removing one closes it.
 *   - Each connection sends the same typed envelope as useAgentWebSocket.
 *   - `lastEvent` is the most recent event from ANY pool, tagged with `_pool_id`.
 *   - `send(text, sessionId)` broadcasts to all connections (mcp-server routes
 *     by session_id on the server side, so any connection will do).
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

interface UseMultiPoolWebSocketReturn {
  /** Current connection status per pool. */
  statuses:  Map<string, PoolConnectionStatus>;
  /** Most recent event from any pool (includes _pool_id). */
  lastEvent: TaggedWsEvent | null;
  /** Send a text message targeting a specific session. */
  send:      (text: string, sessionId: string) => void;
}

function openConnection(
  poolId: string,
  poolStateRef: React.MutableRefObject<Map<string, PoolState>>,
  setStatuses: React.Dispatch<React.SetStateAction<Map<string, PoolConnectionStatus>>>,
  setLastEvent: React.Dispatch<React.SetStateAction<TaggedWsEvent | null>>,
) {
  const params = new URLSearchParams();
  params.set("pool", poolId);
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

      // Schedule reconnect
      s.reconnectTimer = setTimeout(() => {
        const current = poolStateRef.current.get(poolId);
        if (current && !current.intentionalClose) {
          openConnection(poolId, poolStateRef, setStatuses, setLastEvent);
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

export function useMultiPoolWebSocket(activePools: string[]): UseMultiPoolWebSocketReturn {
  const poolStateRef = useRef<Map<string, PoolState>>(new Map());
  const [statuses,  setStatuses]  = useState<Map<string, PoolConnectionStatus>>(new Map());
  const [lastEvent, setLastEvent] = useState<TaggedWsEvent | null>(null);

  // Synchronize open connections with activePools
  useEffect(() => {
    const desired = new Set(activePools);
    const current = new Set(poolStateRef.current.keys());

    // Open new connections
    for (const poolId of desired) {
      if (!current.has(poolId)) {
        openConnection(poolId, poolStateRef, setStatuses, setLastEvent);
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

  const send = useCallback((text: string, sessionId: string) => {
    if (!sessionId) return;
    const envelope = JSON.stringify({
      type:       "message.text",
      session_id: sessionId,
      text,
      timestamp:  new Date().toISOString(),
    });

    // Send on any open connection — mcp-server routes by session_id
    for (const { ws } of poolStateRef.current.values()) {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(envelope);
        return;
      }
    }
  }, []);

  return { statuses, lastEvent, send };
}
