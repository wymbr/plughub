/**
 * App
 * Root component for the Agent Assist UI.
 * Manages all state, drives WebSocket + supervisor polling, and
 * wires up the Header / ChatArea / AgentInput / RightPanel.
 *
 * Layout: full-height flex column
 *   ┌─────────────────────────────────────────┐
 *   │  Header                                 │
 *   ├────────────────────┬────────────────────┤
 *   │  ChatArea (60%)    │  RightPanel (40%)  │
 *   ├────────────────────┴────────────────────┤
 *   │  AgentInput                             │
 *   └─────────────────────────────────────────┘
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActiveTab,
  AppState,
  ChatMessage,
  ClosePayload,
  Toast,
  WsServerEvent,
} from "./types";
import { useAgentWebSocket } from "./hooks/useAgentWebSocket";
import { useSupervisorState } from "./hooks/useSupervisorState";
import { useSupervisorCapabilities } from "./hooks/useSupervisorCapabilities";
import { Header } from "./components/Header";
import { ChatArea } from "./components/ChatArea";
import { AgentInput } from "./components/AgentInput";
import { CloseModal } from "./components/CloseModal";
import { RightPanel } from "./components/RightPanel";
import { ToastContainer } from "./components/ToastContainer";

interface AppProps {
  initialSessionId: string;
  initialContactId: string;
  agentName: string;
  poolId: string;
}

let toastSeq = 0;
function makeToastId(): string {
  return `toast-${++toastSeq}`;
}

const App: React.FC<AppProps> = ({
  initialSessionId,
  initialContactId,
  agentName,
  poolId,
}) => {
  // sessionId starts from URL param; updated when conversation.assigned arrives.
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId || null);
  const [contactId, setContactId] = useState<string>(initialContactId);

  // wsSessionId controls the WebSocket connection. It is intentionally NOT updated
  // when conversation.assigned arrives — the server dynamically subscribes the existing
  // WS connection to the new session's channel, so no client reconnect is needed.
  // It is only set to null when the session ends (to return to lobby/pool mode).
  const [wsSessionId, setWsSessionId] = useState<string | null>(initialSessionId || null);

  // ── WebSocket ────────────────────────────────────────────────────────────
  // Connects via poolId (lobby mode) when wsSessionId is not yet known,
  // or directly via wsSessionId on re-connect after a page refresh with session_id in URL.
  const { status: wsStatus, send, lastEvent } = useAgentWebSocket(
    wsSessionId,
    poolId || null,
  );

  // ── Supervisor polling ────────────────────────────────────────────────────
  const supervisorState = useSupervisorState(sessionId, lastEvent);
  const capabilities = useSupervisorCapabilities(sessionId, supervisorState);

  // ── Local UI state ────────────────────────────────────────────────────────
  const [messages,          setMessages]          = useState<ChatMessage[]>([]);
  const [activeTab,         setActiveTab]         = useState<ActiveTab>("estado");
  const [aiTyping,          setAiTyping]          = useState(false);
  const [sessionClosed,     setSessionClosed]     = useState(false);
  // pendingCloseModal: true when the customer disconnected and the agent still
  // needs to register issue_status / outcome before returning to lobby.
  const [pendingCloseModal, setPendingCloseModal] = useState(false);
  const [toasts,            setToasts]            = useState<Toast[]>([]);
  const aiTypingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Toast helpers ─────────────────────────────────────────────────────────
  const addToast = useCallback(
    (message: string, type: Toast["type"] = "info", persistent = false) => {
      const id = makeToastId();
      setToasts((prev) => [...prev, { id, message, type, persistent }]);
      if (!persistent) {
        setTimeout(() => {
          setToasts((prev) => prev.filter((t) => t.id !== id));
        }, 5000);
      }
    },
    []
  );

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // ── Conversation history loader ───────────────────────────────────────────
  // Fetches the full message list from the REST endpoint and seeds the local
  // messages state. Called on first mount (reconnect with session_id in URL)
  // and whenever a new conversation.assigned arrives.
  const fetchHistory = useCallback(async (sid: string) => {
    try {
      const res = await fetch(`/api/conversation_history/${sid}`)
      if (res.ok) {
        const data = await res.json() as { messages: ChatMessage[] }
        setMessages(data.messages ?? [])
      } else {
        setMessages([])
      }
    } catch {
      // Non-fatal — agent can still work, just starts with no visible history.
      setMessages([])
    }
  }, [])

  // Seed history once on mount when the page is loaded with a session_id already
  // in the URL (e.g. browser refresh while serving a session).
  useEffect(() => {
    if (initialSessionId) {
      fetchHistory(initialSessionId)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []) // intentionally fires only on mount — initialSessionId is a stable prop

  // ── WS event handler ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!lastEvent) return;

    const event: WsServerEvent = lastEvent;

    if (event.type === "connection.accepted") {
      addToast("Conexão estabelecida", "info");
      return;
    }

    // conversation.assigned — arrives via pool:events:{poolId} when the Routing Engine
    // allocates a customer session to this agent's pool. Updates sessionId / contactId
    // so the header and supervisor polling reflect the live session.
    // The WebSocket server already subscribed to agent:events:{session_id} dynamically,
    // so no reconnect is needed here.
    if (event.type === "conversation.assigned") {
      setSessionId(event.session_id);
      if (event.contact_id) setContactId(event.contact_id);
      setSessionClosed(false);
      // Fetch conversation history for this session so the agent sees messages
      // that arrived before they connected (AI turns, prior customer messages).
      // fetchHistory calls setMessages internally, replacing any previous session data.
      fetchHistory(event.session_id);
      // Update URL so browser refresh reconnects directly to this session
      const assignedUrl = new URL(window.location.href);
      assignedUrl.searchParams.set("session_id", event.session_id);
      window.history.replaceState({}, "", assignedUrl.toString());
      addToast("Conversa atribuída", "info");
      return;
    }

    if (event.type === "message.text") {
      const msg: ChatMessage = {
        id: event.message_id,
        author: event.author.type,
        text: event.text,
        timestamp: event.timestamp,
      };
      setMessages((prev) => {
        // Deduplicate by id
        if (prev.some((m) => m.id === msg.id)) return prev;
        return [...prev, msg];
      });
      // Clear ai typing indicator when ai message arrives
      if (event.author.type === "agent_ai") {
        setAiTyping(false);
        if (aiTypingTimerRef.current) {
          clearTimeout(aiTypingTimerRef.current);
          aiTypingTimerRef.current = null;
        }
      }
      return;
    }

    if (event.type === "agent.typing") {
      if (event.author_type === "agent_ai") {
        setAiTyping(true);
        // Auto-clear after 10s if no message arrives
        if (aiTypingTimerRef.current) clearTimeout(aiTypingTimerRef.current);
        aiTypingTimerRef.current = setTimeout(() => {
          setAiTyping(false);
        }, 10_000);
      }
      return;
    }

    if (event.type === "session.closed") {
      if (event.reason === "client_disconnect") {
        // Customer hung up — keep sessionId so handleClose can still call
        // agent_done. Block input and show the modal so the agent registers the outcome.
        setSessionClosed(true);
        setPendingCloseModal(true);
        addToast("Cliente desconectou. Preencha o encerramento.", "warning", true);
      } else if (sessionId !== null) {
        // Server-initiated close (e.g. timeout) while we still have an active session.
        // If sessionId is already null, handleClose already cleaned up — skip the echo
        // to avoid duplicate toasts and redundant state transitions.
        setSessionClosed(false);   // return to lobby-ready state
        setSessionId(null);
        setWsSessionId(null);
        setPendingCloseModal(false);
        const closedUrl = new URL(window.location.href);
        closedUrl.searchParams.delete("session_id");
        window.history.replaceState({}, "", closedUrl.toString());
        addToast(`Sessão encerrada pelo servidor: ${event.reason}`, "warning", true);
      }
      return;
    }

    if (event.type === "menu.render") {
      // Menu renders from the AI agent arrive over WS — show as system message.
      // For button/list/checklist interactions, list the options below the prompt
      // so the human agent can see what was offered to the customer.
      let menuText = event.prompt
      if (event.options && event.options.length > 0) {
        const optLines = event.options.map((o) => `  • ${o.label}`).join("\n")
        menuText = `${event.prompt}\n${optLines}`
      } else if (event.fields && event.fields.length > 0) {
        const fieldLines = event.fields.map((f) => `  • ${f.label}`).join("\n")
        menuText = `${event.prompt} [form]\n${fieldLines}`
      }
      const menuMsg: ChatMessage = {
        id: `menu-${event.menu_id}`,
        author: "system",
        text: menuText,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => {
        if (prev.some((m) => m.id === menuMsg.id)) return prev;
        return [...prev, menuMsg];
      });
      return;
    }
  }, [lastEvent, addToast, fetchHistory]);

  // Cleanup typing timer on unmount
  useEffect(() => {
    return () => {
      if (aiTypingTimerRef.current) clearTimeout(aiTypingTimerRef.current);
    };
  }, []);

  // ── Send handler ──────────────────────────────────────────────────────────
  const handleSend = useCallback(
    (text: string) => {
      send(text);
      // Optimistically add the agent's own message to the chat
      const msg: ChatMessage = {
        id: `local-${Date.now()}`,
        author: "agent_human",
        text,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, msg]);
    },
    [send]
  );

  // ── Close session handler ─────────────────────────────────────────────────
  // Called from:
  //   (a) AgentInput's CloseModal — agent-initiated close
  //   (b) pendingCloseModal — agent registers outcome after customer disconnect
  const handleClose = useCallback(
    async (payload: ClosePayload) => {
      try {
        await fetch(`/api/agent_done/${sessionId}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        // Return to lobby immediately — don't wait for the session.closed WS echo.
        // The echo will arrive but sessionId will already be null, so the handler
        // skips it (sessionId !== null guard). Using false here so the UI returns
        // to "waiting for assignment" state instead of remaining visually locked.
        setSessionClosed(false);
        setPendingCloseModal(false);
        setSessionId(null);
        setWsSessionId(null);
        const doneUrl = new URL(window.location.href);
        doneUrl.searchParams.delete("session_id");
        window.history.replaceState({}, "", doneUrl.toString());
        addToast("Atendimento encerrado com sucesso.", "info");
      } catch {
        addToast("Erro ao encerrar atendimento. Tente novamente.", "error");
      }
    },
    [sessionId, addToast]
  );

  // ── Escalate / invite agent stubs ─────────────────────────────────────────
  const handleInviteAgent = useCallback(
    (agentTypeId: string) => {
      addToast(`Convite enviado para: ${agentTypeId}`, "info");
    },
    [addToast]
  );

  const handleEscalate = useCallback(
    (targetPoolId: string) => {
      addToast(`Escalando para pool: ${targetPoolId}`, "warning");
    },
    [addToast]
  );

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-screen overflow-hidden bg-gray-50">
      <Header
        agentName={agentName}
        poolId={poolId}
        sessionId={sessionId}
        wsStatus={wsStatus}
        sla={supervisorState?.sla ?? null}
      />

      <div className="flex flex-1 overflow-hidden">
        {/* Chat — 60% */}
        <div className="flex flex-col w-[60%] overflow-hidden">
          {/* Lobby overlay — shown when no session is active */}
          {!sessionId && !pendingCloseModal && (
            <div className="flex-1 flex items-center justify-center text-gray-400 text-sm select-none">
              <span className="animate-pulse">⏳ Aguardando próximo atendimento…</span>
            </div>
          )}
          {(sessionId || pendingCloseModal) && (
            <ChatArea
              messages={messages}
              aiTyping={aiTyping}
              liveState={supervisorState ? {
                sentimentScore: supervisorState.sentiment.current,
                sentimentAlert: supervisorState.sentiment.alert,
                sentimentTrend: supervisorState.sentiment.trend,
                intent:         supervisorState.intent.current,
                flags:          supervisorState.flags,
              } : null}
            />
          )}
          <AgentInput
            onSend={handleSend}
            onClose={handleClose}
            disabled={!sessionId || sessionClosed || wsStatus === "disconnected"}
          />
        </div>

        {/* Right panel — 40% */}
        <div className="w-[40%] overflow-hidden">
          <RightPanel
            activeTab={activeTab}
            onTabChange={setActiveTab}
            supervisorState={supervisorState}
            capabilities={capabilities}
            onInviteAgent={handleInviteAgent}
            onEscalate={handleEscalate}
          />
        </div>
      </div>

      {/* Modal de encerramento quando o cliente desconecta primeiro.
          O agente deve preencher issue_status / outcome antes de voltar ao lobby. */}
      {pendingCloseModal && (
        <CloseModal
          defaultIssueStatus="Cliente desconectou"
          defaultOutcome="abandoned"
          onConfirm={(payload) => handleClose(payload)}
          onCancel={() =>
            // Cancel auto-submits with defaults so agent_done is always called
            handleClose({ issue_status: "Cliente desconectou", outcome: "abandoned" })
          }
        />
      )}

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
};

export default App;
