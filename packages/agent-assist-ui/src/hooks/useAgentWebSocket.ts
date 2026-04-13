/**
 * useAgentWebSocket
 * Manages the WebSocket connection to the mcp-server-plughub agent channel.
 * Provides inbound message stream and an outbound send function.
 * Spec: agent-assist-piloto.md — Conexão e polling / WebSocket section
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { WsServerEvent } from "../types";

const WS_BASE = import.meta.env.VITE_MCP_WS_URL ?? "/agent-ws";

export type WsStatus = "connecting" | "connected" | "disconnected";

interface UseAgentWebSocketReturn {
  status: WsStatus;
  send: (text: string) => void;
  lastEvent: WsServerEvent | null;
}

export function useAgentWebSocket(
  sessionId: string | null,
  poolId: string | null,
): UseAgentWebSocketReturn {
  const wsRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<WsStatus>("disconnected");
  const [lastEvent, setLastEvent] = useState<WsServerEvent | null>(null);

  useEffect(() => {
    // Connect as soon as we have either a session (re-connect) or a pool (lobby mode).
    if (!sessionId && !poolId) return;

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
    };

    // Ping/pong heartbeat
    const heartbeat = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "pong" }));
      }
    }, 30_000);

    return () => {
      clearInterval(heartbeat);
      ws.close();
    };
    // Only connect once: when the initial identifier (pool or session) becomes available.
    // If the server receives conversation.assigned it dynamically adds the session
    // subscription — no reconnect needed from the client side.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId ?? poolId]);

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
