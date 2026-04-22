/**
 * useAgentWebSocket
 * Manages the WebSocket connection to the mcp-server-plughub agent channel.
 * Provides inbound message stream and an outbound send function.
 * Spec: agent-assist-piloto.md — Conexão e polling / WebSocket section
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { WsServerEvent, WsStatus } from "../types";

const WS_BASE = import.meta.env.VITE_MCP_WS_URL ?? "/agent-ws";
const RECONNECT_DELAY_MS = 3_000;

interface UseAgentWebSocketReturn {
  status: WsStatus;
  send: (text: string) => void;
  lastEvent: WsServerEvent | null;
}

export function useAgentWebSocket(
  sessionId: string | null,
  poolId: string | null,
): UseAgentWebSocketReturn {
  const wsRef        = useRef<WebSocket | null>(null);
  const [status,     setStatus]     = useState<WsStatus>("disconnected");
  const [lastEvent,  setLastEvent]  = useState<WsServerEvent | null>(null);
  // reconnectCount is incremented on unexpected close to trigger a reconnect.
  const [reconnectCount, setReconnectCount] = useState(0);
  // intentionalClose is set to true in the cleanup function so that the
  // onclose handler doesn't schedule another reconnect on deliberate teardown.
  const intentionalClose = useRef(false);

  useEffect(() => {
    // Connect as soon as we have either a session (re-connect) or a pool (lobby mode).
    if (!sessionId && !poolId) return;

    intentionalClose.current = false;

    // Always pass both params when available so the server subscribes to
    // both agent:events:{session_id} AND pool:events:{pool} simultaneously.
    // This ensures a new conversation.assigned is received even when the agent
    // reconnects with a stale session_id in the URL after a browser refresh.
    const params = new URLSearchParams();
    if (sessionId) params.set("session_id", sessionId);
    if (poolId)    params.set("pool", poolId);
    const url = `${WS_BASE}?${params.toString()}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;
    setStatus("connecting");

    ws.onopen = () => {
      setStatus("connected");
    };

    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as WsServerEvent;
        setLastEvent(data);
      } catch {
        // ignore malformed messages
      }
    };

    ws.onerror = () => {
      setStatus("disconnected");
    };

    ws.onclose = () => {
      setStatus("disconnected");
      // Auto-reconnect after RECONNECT_DELAY_MS unless the close was intentional
      // (component unmount, session end). This keeps the subscription alive even
      // if the mcp-server restarts mid-session.
      if (!intentionalClose.current) {
        setTimeout(() => {
          setReconnectCount((n) => n + 1);
        }, RECONNECT_DELAY_MS);
      }
    };

    // Ping/pong heartbeat
    const heartbeat = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "pong" }));
      }
    }, 30_000);

    return () => {
      intentionalClose.current = true;
      clearInterval(heartbeat);
      ws.close();
    };
    // Re-run when the session/pool identifier changes OR when an unexpected close
    // triggers a reconnect (reconnectCount bump).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId ?? poolId, reconnectCount]);

  const send = useCallback(
    (text: string) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(
        JSON.stringify({
          type: "message.text",
          text,
          timestamp: new Date().toISOString(),
        })
      );
    },
    []
  );

  return { status, send, lastEvent };
}
