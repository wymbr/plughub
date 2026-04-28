/**
 * AgentAssistPage — Multi-contact Agent Assist UI (platform-ui module)
 *
 * Adapted from packages/agent-assist-ui/src/App.tsx for the platform-ui shell.
 * Key differences from the standalone app:
 *   - Uses `h-full` (not h-screen) — Shell provides the outer container
 *   - agentName sourced from useAuth() session.name
 *   - poolId read from ?pool= URL param (useSearchParams); shows picker if absent
 *
 * Layout:
 *   ┌──────────────────────────────────────────────────────┐
 *   │  Header (agente, pool, sessão, SLA, WS)              │
 *   ├──────────┬───────────────────────┬───────────────────┤
 *   │ Contact  │  Chat Area            │  Right Panel      │
 *   │ List     │  (selected contact)   │  (context)        │
 *   │  (20%)   │       (50%)           │       (30%)       │
 *   ├──────────┴───────────────────────┴───────────────────┤
 *   │  AgentInput  (tied to selected contact)              │
 *   └──────────────────────────────────────────────────────┘
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "@/auth/useAuth";

import {
  ActiveTab,
  ChatMessage,
  ClosePayload,
  ContactSession,
  Toast,
  WsServerEvent,
} from "./types";
import { useAgentWebSocket }         from "./hooks/useAgentWebSocket";
import { useSupervisorState }        from "./hooks/useSupervisorState";
import { useSupervisorCapabilities } from "./hooks/useSupervisorCapabilities";
import { Header }         from "./components/Header";
import { ChatArea }       from "./components/ChatArea";
import { AgentInput }     from "./components/AgentInput";
import { CloseModal }     from "./components/CloseModal";
import { RightPanel }     from "./components/RightPanel";
import { ContactList }    from "./components/ContactList";
import { ToastContainer } from "./components/ToastContainer";

// ── Toast id generator ─────────────────────────────────────────────────────
let toastSeq = 0;
function makeToastId(): string { return `toast-${++toastSeq}`; }

// ── ContactSession factory ─────────────────────────────────────────────────
function makeContact(sessionId: string, channel = "webchat"): ContactSession {
  return {
    sessionId,
    contactId:         null,
    customerName:      null,
    channel,
    messages:          [],
    supervisorState:   null,
    capabilities:      null,
    sessionStartedAt:  new Date(),
    unreadCount:       0,
    sessionClosed:     false,
    pendingCloseModal: false,
  };
}

// ── Pool picker (shown when ?pool= is not set) ─────────────────────────────
const PoolPicker: React.FC<{ onPick: (pool: string) => void }> = ({ onPick }) => {
  const [value, setValue] = useState("");
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-gray-500">
      <span className="text-4xl">🤖</span>
      <p className="text-sm">Informe o pool para iniciar o atendimento</p>
      <div className="flex gap-2">
        <input
          type="text"
          className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          placeholder="Ex: retencao_humano"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && value.trim() && onPick(value.trim())}
        />
        <button
          disabled={!value.trim()}
          onClick={() => onPick(value.trim())}
          className="px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium disabled:opacity-40"
        >
          Entrar
        </button>
      </div>
    </div>
  );
};

// ── AgentAssistPage ────────────────────────────────────────────────────────
export const AgentAssistPage: React.FC = () => {
  const { session } = useAuth();
  const agentName   = session?.name ?? "Agente";

  const [searchParams, setSearchParams] = useSearchParams();
  const [poolId, setPoolId] = useState<string>(searchParams.get("pool") ?? "");

  // Commit pool selection to URL
  const handlePickPool = useCallback((pool: string) => {
    setPoolId(pool);
    setSearchParams((p) => { p.set("pool", pool); return p; }, { replace: true });
  }, [setSearchParams]);

  // ── Multi-contact state ─────────────────────────────────────────────────
  const [contacts, setContacts] = useState<Map<string, ContactSession>>(new Map());
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

  // Refs always hold latest value — used inside WS event handler to avoid
  // stale closure bugs (the handler's dep array omits these on purpose).
  const contactsRef        = useRef<Map<string, ContactSession>>(new Map());
  const selectedSessionRef = useRef<string | null>(null);
  useEffect(() => { contactsRef.current        = contacts;          }, [contacts]);
  useEffect(() => { selectedSessionRef.current = selectedSessionId; }, [selectedSessionId]);

  // ── Shared UI state ─────────────────────────────────────────────────────
  const [activeTab, setActiveTab] = useState<ActiveTab>("estado");
  const [toasts, setToasts]       = useState<Toast[]>([]);

  // ── WebSocket — one connection for all contacts ─────────────────────────
  const { status: wsStatus, send, lastEvent } = useAgentWebSocket(poolId || null);

  // Track assignments to suppress routing-drain duplicates
  const notifiedAssignments = useRef<Set<string>>(new Set());
  // Pending closed sessions: session.closed may arrive before conversation.assigned
  const pendingClosedSessions = useRef<Map<string, string>>(new Map());
  // Sessions already handled — suppress routing drain re-emissions
  const handledSessions = useRef<Set<string>>(new Set());

  // ── Supervisor hooks — scoped to selected contact ───────────────────────
  const supervisorState = useSupervisorState(selectedSessionId, lastEvent);
  const capabilities    = useSupervisorCapabilities(selectedSessionId, supervisorState);

  // Sync supervisor data back into the selected contact's state
  useEffect(() => {
    if (!selectedSessionId) return;
    setContacts(prev => {
      const c = prev.get(selectedSessionId);
      if (!c) return prev;
      const next = new Map(prev);
      next.set(selectedSessionId, {
        ...c,
        supervisorState: supervisorState ?? c.supervisorState,
        capabilities:    capabilities    ?? c.capabilities,
        customerName:    c.customerName,
      });
      return next;
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [supervisorState, capabilities]);

  // AI typing timer per session
  const aiTypingTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const [aiTypingSessions, setAiTypingSessions] = useState<Set<string>>(new Set());

  // ── Toast helpers ───────────────────────────────────────────────────────
  const addToast = useCallback(
    (message: string, type: Toast["type"] = "info", persistent = false) => {
      const id = makeToastId();
      setToasts(prev => [...prev, { id, message, type, persistent }]);
      if (!persistent) {
        setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 5000);
      }
    },
    []
  );

  const dismissToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  // ── History loader ──────────────────────────────────────────────────────
  const fetchHistory = useCallback(async (sessionId: string) => {
    try {
      const res  = await fetch(`/api/conversation_history/${sessionId}`);
      const data = res.ok
        ? (await res.json() as { messages: ChatMessage[] })
        : { messages: [] };
      setContacts(prev => {
        const c = prev.get(sessionId);
        if (!c) return prev;
        const next = new Map(prev);
        next.set(sessionId, { ...c, messages: data.messages ?? [] });
        return next;
      });
    } catch {
      // non-fatal — agent starts with no visible history
    }
  }, []);

  // ── WS event handler ────────────────────────────────────────────────────
  useEffect(() => {
    if (!lastEvent) return;
    const event: WsServerEvent = lastEvent;

    if (event.type === "connection.accepted") return;

    // ── New contact assigned ────────────────────────────────────────────
    if (event.type === "conversation.assigned") {
      const { session_id, contact_id } = event;

      if (handledSessions.current.has(session_id)) return;

      const isNew = !contactsRef.current.has(session_id) &&
                    !notifiedAssignments.current.has(session_id);
      notifiedAssignments.current.add(session_id);

      setContacts(prev => {
        if (prev.has(session_id)) return prev;
        const next = new Map(prev);
        const alreadyClosed = pendingClosedSessions.current.has(session_id);
        pendingClosedSessions.current.delete(session_id);
        next.set(session_id, {
          ...makeContact(session_id),
          contactId:         contact_id ?? null,
          sessionClosed:     alreadyClosed,
          pendingCloseModal: alreadyClosed,
        });
        return next;
      });
      if (!isNew) return;
      setSelectedSessionId(prev => prev ?? session_id);
      fetchHistory(session_id);
      addToast("Novo contato atribuído", "info");
      return;
    }

    // ── Incoming message ────────────────────────────────────────────────
    if (event.type === "message.text") {
      const sid = (event as unknown as Record<string, unknown>)["session_id"] as string | undefined;
      if (!sid) return;

      const msg: ChatMessage = {
        id:          event.message_id,
        author:      event.author.type,
        agentTypeId: event.author.agent_type_id,
        text:        event.text,
        timestamp:   event.timestamp,
        visibility:  event.visibility,
      };

      setContacts(prev => {
        const c = prev.get(sid);
        if (!c) return prev;
        if (c.messages.some(m => m.id === msg.id)) return prev;
        const isSelected = sid === selectedSessionRef.current;
        const next = new Map(prev);
        next.set(sid, {
          ...c,
          messages:    [...c.messages, msg],
          unreadCount: isSelected ? 0 : c.unreadCount + 1,
        });
        return next;
      });

      // Clear AI typing indicator for this session
      if (event.author.type === "agent_ai") {
        const timer = aiTypingTimers.current.get(sid);
        if (timer) { clearTimeout(timer); aiTypingTimers.current.delete(sid); }
        setAiTypingSessions(prev => { const s = new Set(prev); s.delete(sid); return s; });
      }
      return;
    }

    // ── AI typing indicator ─────────────────────────────────────────────
    if (event.type === "agent.typing" && event.author_type === "agent_ai") {
      const sid = (event as unknown as Record<string, unknown>)["session_id"] as string | undefined;
      if (!sid) return;
      setAiTypingSessions(prev => new Set(prev).add(sid));
      const existing = aiTypingTimers.current.get(sid);
      if (existing) clearTimeout(existing);
      aiTypingTimers.current.set(
        sid,
        setTimeout(() => {
          setAiTypingSessions(prev => { const s = new Set(prev); s.delete(sid); return s; });
          aiTypingTimers.current.delete(sid);
        }, 10_000)
      );
      return;
    }

    // ── Session closed ──────────────────────────────────────────────────
    if (event.type === "session.closed") {
      const sid = (event as unknown as Record<string, unknown>)["session_id"] as string | undefined;
      if (!sid) return;

      if (event.reason === "client_disconnect") {
        pendingClosedSessions.current.set(sid, event.reason);
        setContacts(prev => {
          const c = prev.get(sid);
          if (!c) return prev;
          const next = new Map(prev);
          next.set(sid, { ...c, sessionClosed: true, pendingCloseModal: true });
          return next;
        });
        if (!notifiedAssignments.current.has(`closed:${sid}`)) {
          notifiedAssignments.current.add(`closed:${sid}`);
          addToast("Cliente desconectou. Preencha o encerramento.", "warning", true);
        }
      } else {
        pendingClosedSessions.current.delete(sid);
        setContacts(prev => {
          const next = new Map(prev);
          next.delete(sid);
          return next;
        });
        setSelectedSessionId(prev => {
          if (prev !== sid) return prev;
          const remaining = [...contactsRef.current.keys()].filter(k => k !== sid);
          return remaining[0] ?? null;
        });
      }
      return;
    }

    // ── Menu render ─────────────────────────────────────────────────────
    if (event.type === "menu.render") {
      const sid = (event as unknown as Record<string, unknown>)["session_id"] as string | undefined;
      if (!sid) return;
      const menuMsg: ChatMessage = {
        id:        `menu-${event.menu_id}`,
        author:    "system",
        text:      event.prompt,
        timestamp: new Date().toISOString(),
        menuData: {
          menu_id:     event.menu_id,
          interaction: event.interaction,
          prompt:      event.prompt,
          options:     event.options,
          fields:      event.fields,
        },
      };
      setContacts(prev => {
        const c = prev.get(sid);
        if (!c) return prev;
        if (c.messages.some(m => m.id === menuMsg.id)) return prev;
        const next = new Map(prev);
        next.set(sid, { ...c, messages: [...c.messages, menuMsg] });
        return next;
      });
      return;
    }

    // ── @mention command acknowledgement ─────────────────────────────────
    if (event.type === "mention_command.ack") {
      const { session_id: sid, command } = event;
      if (!sid || !command) return;
      const ackMsg: ChatMessage = {
        id:         `ack-${command}-${Date.now()}`,
        author:     "system",
        text:       `✓ @copilot reconheceu o comando "${command}"`,
        timestamp:  new Date().toISOString(),
        visibility: "agents_only",
      };
      setContacts(prev => {
        const c = prev.get(sid);
        if (!c) return prev;
        const next = new Map(prev);
        next.set(sid, { ...c, messages: [...c.messages, ackMsg] });
        return next;
      });
      return;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastEvent, addToast, fetchHistory]);

  // Clear typing timers on unmount
  useEffect(() => {
    return () => {
      for (const t of aiTypingTimers.current.values()) clearTimeout(t);
    };
  }, []);

  // Mark messages as read when switching contact
  const handleSelectContact = useCallback((sessionId: string) => {
    setSelectedSessionId(sessionId);
    setActiveTab("estado");
    setContacts(prev => {
      const c = prev.get(sessionId);
      if (!c || c.unreadCount === 0) return prev;
      const next = new Map(prev);
      next.set(sessionId, { ...c, unreadCount: 0 });
      return next;
    });
  }, []);

  // ── Send handler ────────────────────────────────────────────────────────
  const handleSend = useCallback(
    (text: string) => {
      if (!selectedSessionId) return;
      send(text, selectedSessionId);
      const isMention = text.trimStart().startsWith("@");
      const msg: ChatMessage = {
        id:         `local-${Date.now()}`,
        author:     "agent_human",
        text,
        timestamp:  new Date().toISOString(),
        visibility: isMention ? "agents_only" : undefined,
      };
      setContacts(prev => {
        const c = prev.get(selectedSessionId);
        if (!c) return prev;
        const next = new Map(prev);
        next.set(selectedSessionId, { ...c, messages: [...c.messages, msg] });
        return next;
      });
    },
    [send, selectedSessionId]
  );

  // ── Close session handler ───────────────────────────────────────────────
  const handleClose = useCallback(
    async (sessionId: string, payload: ClosePayload) => {
      handledSessions.current.add(sessionId);
      try {
        await fetch(`/api/agent_done/${sessionId}`, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify(payload),
        });
        setContacts(prev => {
          const next = new Map(prev);
          next.delete(sessionId);
          return next;
        });
        setSelectedSessionId(prev => {
          if (prev !== sessionId) return prev;
          const remaining = [...contactsRef.current.keys()].filter(k => k !== sessionId);
          return remaining[0] ?? null;
        });
        addToast("Atendimento encerrado.", "info");
      } catch {
        addToast("Erro ao encerrar atendimento.", "error");
      }
    },
    [addToast]
  );

  // ── Escalate / invite stubs ─────────────────────────────────────────────
  const handleInviteAgent = useCallback(
    (agentTypeId: string) => addToast(`Convite enviado: ${agentTypeId}`, "info"),
    [addToast]
  );
  const handleEscalate = useCallback(
    (targetPoolId: string) => addToast(`Escalando para: ${targetPoolId}`, "warning"),
    [addToast]
  );

  // ── Selected contact snapshot ───────────────────────────────────────────
  const selected = selectedSessionId ? contacts.get(selectedSessionId) ?? null : null;

  // ── Pool not set — show picker ──────────────────────────────────────────
  if (!poolId) {
    return (
      <div className="h-full overflow-hidden bg-gray-50">
        <PoolPicker onPick={handlePickPool} />
      </div>
    );
  }

  // ── Main render ─────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full overflow-hidden bg-gray-50">
      <Header
        agentName={agentName}
        poolId={poolId}
        sessionId={selectedSessionId}
        wsStatus={wsStatus}
        sla={selected?.supervisorState?.sla ?? null}
        sessionStartedAt={selected?.sessionStartedAt ?? null}
      />

      <div className="flex flex-1 overflow-hidden">
        {/* Contact list — 20% */}
        <div className="w-[20%] border-r border-gray-200 overflow-hidden flex flex-col">
          <ContactList
            contacts={[...contacts.values()]}
            selectedSessionId={selectedSessionId}
            aiTypingSessions={aiTypingSessions}
            onSelect={handleSelectContact}
          />
        </div>

        {/* Chat — 50% */}
        <div className="flex flex-col w-[50%] overflow-hidden">
          {!selected ? (
            <div className="flex-1 flex items-center justify-center text-gray-400 text-sm select-none">
              <span className="animate-pulse">⏳ Aguardando próximo atendimento…</span>
            </div>
          ) : (
            <ChatArea
              messages={selected.messages}
              aiTyping={aiTypingSessions.has(selected.sessionId)}
              sessionClosed={selected.sessionClosed}
              liveState={selected.supervisorState ? {
                sentimentScore: selected.supervisorState.sentiment.current,
                sentimentAlert: selected.supervisorState.sentiment.alert,
                sentimentTrend: selected.supervisorState.sentiment.trend,
                intent:         selected.supervisorState.intent.current,
                flags:          selected.supervisorState.flags,
              } : null}
            />
          )}
          <AgentInput
            onSend={handleSend}
            onClose={(payload) => selected && handleClose(selected.sessionId, payload)}
            disabled={!selected}
            sessionClosed={selected?.sessionClosed ?? false}
          />
        </div>

        {/* Right panel — 30% */}
        <div className="w-[30%] overflow-hidden flex flex-col">
          <RightPanel
            activeTab={activeTab}
            onTabChange={setActiveTab}
            supervisorState={selected?.supervisorState ?? null}
            capabilities={selected?.capabilities ?? null}
            customerId={selected?.contactId ?? null}
            onInviteAgent={handleInviteAgent}
            onEscalate={handleEscalate}
          />
        </div>
      </div>

      {/* Close modal — shown when customer disconnected on selected contact */}
      {selected?.pendingCloseModal && (
        <CloseModal
          defaultIssueStatus="Cliente desconectou"
          defaultOutcome="abandoned"
          onConfirm={(payload) => handleClose(selected.sessionId, payload)}
          onCancel={() =>
            handleClose(selected.sessionId, {
              issue_status: "Cliente desconectou",
              outcome: "abandoned",
            })
          }
        />
      )}

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
};
