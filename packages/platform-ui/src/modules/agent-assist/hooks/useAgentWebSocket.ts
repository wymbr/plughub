/**
 * useAgentWebSocket
 * Manages the WebSocket connection to the mcp-server-plughub agent channel.
 *
 * Multi-contact design:
 *   - One persistent WebSocket connection per agent session (not per contact).
 *   - The connection is stable: it does NOT reconnect when contacts are assigned
 *     or closed. The server subscribes/unsubscribes Redis channels dynamically.
 *   - `send(text, sessionId)` requires the caller to specify which session to
 *     target — the server uses this to route the message to the right contact.
 *   - All incoming events carry `session_id` so the App can demultiplex them
 *     to the correct ContactSession in the Map.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { WsServerEvent, WsStatus } from "../types";

const WS_BASE = import.meta.env.VITE_MCP_WS_URL ?? "/agent-ws";
const RECONNECT_DELAY_MS = 3_000;

interface UseAgentWebSocketReturn {
  status:    WsStatus;
  /** Send a text message to a specific session. sessionId is mandatory. */
  send:      (text: string, sessionId: string) => void;
  lastEvent: WsServerEvent | null;
}

export function useAgentWebSocket(
  poolId: string | null,
): UseAgentWebSocketReturn {
  const wsRef        = useRef<WebSocket | null>(null);
  const [status,     setStatus]     = useState<WsStatus>("disconnected");
  const [lastEvent,  setLastEvent]  = useState<WsServerEvent | null>(null);
  // reconnectCount is bumped on unexpected close to trigger a reconnect.
  const [reconnectCount, setReconnectCount] = useState(0);
  // intentionalClose is set to true in the cleanup so onclose doesn't
  // schedule a spurious reconnect on deliberate teardown (unmount).
  const intentionalClose   = useRef(false);
  // Debounce timer for the "disconnected" visual state — brief reconnects
  // (e.g. Vite proxy resets < 2 s) should not flicker the header.
  const disconnectTimer    = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!poolId) return;

    intentionalClose.current = false;

    // Connect with pool only — the server subscribes to session channels
    // dynamically as conversation.assigned events arrive. We never pass
    // session_id here because the connection outlives any single session.
    const params = new URLSearchParams();
    params.set("pool", poolId);
    const url = `${WS_BASE}?${params.toString()}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;
    // Show "connecting" immediately only if we weren't already connected
    // (avoids flicker when a quick reconnect happens in < 2 s).
    setStatus(prev => prev === "connected" ? "connected" : "connecting");

    ws.onopen = () => {
      if (disconnectTimer.current) {
        clearTimeout(disconnectTimer.current);
        disconnectTimer.current = null;
      }
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
      // Don't immediately set disconnected — let onclose handle it with debounce
    };

    ws.onclose = () => {
      if (!intentionalClose.current) {
        // Debounce the "disconnected" status: only show it if the reconnect
        // takes longer than 2 s. Quick proxy resets stay invisible.
        disconnectTimer.current = setTimeout(() => {
          setStatus("disconnected");
        }, 2_000);
        setTimeout(() => {
          setReconnectCount((n) => n + 1);
        }, RECONNECT_DELAY_MS);
      } else {
        setStatus("disconnected");
      }
    };

    // Ping/pong heartbeat — keeps the connection alive through proxies.
    const heartbeat = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "pong" }));
      }
    }, 30_000);

    return () => {
      intentionalClose.current = true;
      clearInterval(heartbeat);
      if (disconnectTimer.current) {
        clearTimeout(disconnectTimer.current);
        disconnectTimer.current = null;
      }
      ws.close();
    };
    // Reconnect only when poolId changes (rare) or after an unexpected drop.
    // Assigning/closing contacts does NOT trigger a reconnect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [poolId, reconnectCount]);

  const send = useCallback(
    (text: string, sessionId: string) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (!sessionId) return;  // refuse to send without a target session
      ws.send(
        JSON.stringify({
          type:       "message.text",
          session_id: sessionId,
          text,
          timestamp:  new Date().toISOString(),
        })
      );
    },
    []
  );

  return { status, send, lastEvent };
}
