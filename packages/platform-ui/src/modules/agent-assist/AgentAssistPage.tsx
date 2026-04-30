/**
 * AgentAssistPage — Multi-contact, Multi-pool Agent Assist UI
 *
 * Architecture (multi-pool):
 *   - Agent can be "Ready" in multiple pools simultaneously.
 *   - One WebSocket per active pool (useMultiPoolWebSocket).
 *   - Contacts from all pools appear in a single ContactList (FIFO order).
 *   - Pool presence controls live in the Header (second row of pills).
 *
 * Layout:
 *   ┌────────────────────────────────────────────────────────────┐
 *   │  Header row 1: agente, pool, sessão, SLA, WS status        │
 *   │  Header row 2: pool pills (Ready/Offline toggles)          │
 *   ├──────────┬──────────────────────────┬─────────────────────┤
 *   │  Contact │  Chat Area               │  Right Panel        │
 *   │  List    │  (selected contact)      │  (context)          │
 *   │ (~200px) │     (flex-1)             │   (~280px)          │
 *   ├──────────┴──────────────────────────┴─────────────────────┤
 *   │  AgentInput  (tied to selected contact)                    │
 *   └────────────────────────────────────────────────────────────┘
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/auth/useAuth";

import {
  ActiveTab,
  ChatMessage,
  ClosePayload,
  ContactSession,
  PoolConnectionStatus,
  PoolInfo,
  Toast,
  WsStatus,
} from "./types";
import { useMultiPoolWebSocket } from "./hooks/useMultiPoolWebSocket";
import { useSupervisorState }        from "./hooks/useSupervisorState";
import { useSupervisorCapabilities } from "./hooks/useSupervisorCapabilities";
import { Header }           from "./components/Header";
import { ActionBar }        from "./components/ActionBar";
import { ChatArea }         from "./components/ChatArea";
import { AgentInput }       from "./components/AgentInput";
import { CloseModal }       from "./components/CloseModal";
import { RightPanel }       from "./components/RightPanel";
import { ContactList }      from "./components/ContactList";
import { ToastContainer }   from "./components/ToastContainer";

const API_BASE = import.meta.env.VITE_REGISTRY_URL ?? "/v1";

// ── Toast id generator ─────────────────────────────────────────────────────
let toastSeq = 0;
function makeToastId(): string { return `toast-${++toastSeq}`; }

// ── ContactSession factory ─────────────────────────────────────────────────
function makeContact(sessionId: string, poolId: string, channel = "webchat"): ContactSession {
  return {
    sessionId,
    contactId:         null,
    customerName:      null,
    channel,
    poolId,
    slaTargetMs:       null,
    messages:          [],
    supervisorState:   null,
    capabilities:      null,
    sessionStartedAt:  new Date(),
    unreadCount:       0,
    sessionClosed:     false,
    pendingCloseModal: false,
  };
}

// ── Aggregate WS status helper ─────────────────────────────────────────────
function aggregateStatus(
  statuses: Map<string, PoolConnectionStatus>,
  activePools: string[],
): WsStatus {
  if (activePools.length === 0) return "disconnected";
  const vals = activePools.map(p => statuses.get(p) ?? "disconnected");
  if (vals.some(v => v === "connected"))    return "connected";
  if (vals.some(v => v === "connecting"))   return "connecting";
  return "disconnected";
}

// ── Fetch pools from agent-registry ───────────────────────────────────────
async function fetchPools(accessiblePools: string[]): Promise<PoolInfo[]> {
  try {
    const res  = await fetch(`${API_BASE}/pools`, {
      headers: { "x-tenant-id": "tenant_demo", "x-user-id": "operator" },
    });
    if (!res.ok) return [];
    const data = await res.json() as Array<{
      pool_id: string;
      display_name?: string;
      channel_types?: string[];
      sla_target_ms?: number | null;
    }>;
    const list: PoolInfo[] = data.map(p => ({
      pool_id:       p.pool_id,
      display_name:  p.display_name,
      channel_types: p.channel_types ?? [],
      sla_target_ms: p.sla_target_ms ?? null,
    }));
    // Filter to only pools the agent is authorised for
    if (accessiblePools.length === 0) return list;
    return list.filter(p => accessiblePools.includes(p.pool_id));
  } catch {
    return [];
  }
}

// ── AgentAssistPage ────────────────────────────────────────────────────────
export const AgentAssistPage: React.FC = () => {
  const { session } = useAuth();
  const agentName   = session?.name ?? "Agente";
  const accessiblePools: string[] = session?.accessiblePools ?? [];

  // ── Available pools (from registry) ────────────────────────────────────
  const [availablePools, setAvailablePools] = useState<PoolInfo[]>([]);
  useEffect(() => {
    fetchPools(accessiblePools).then(setAvailablePools);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Presence: set of pool_ids the agent is "Ready" in ──────────────────
  const [activePools, setActivePools] = useState<string[]>([]);

  const handleTogglePool = useCallback((poolId: string) => {
    setActivePools(prev =>
      prev.includes(poolId) ? prev.filter(p => p !== poolId) : [...prev, poolId]
    );
  }, []);

  const handleJoinAll = useCallback(() => {
    setActivePools(availablePools.map(p => p.pool_id));
  }, [availablePools]);

  // ── Multi-pool WebSocket ────────────────────────────────────────────────
  const { statuses, lastEvent, send } = useMultiPoolWebSocket(activePools);

  // ── Multi-contact state ─────────────────────────────────────────────────
  const [contacts, setContacts] = useState<Map<string, ContactSession>>(new Map());
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

  const contactsRef        = useRef<Map<string, ContactSession>>(new Map());
  const selectedSessionRef = useRef<string | null>(null);
  useEffect(() => { contactsRef.current        = contacts;          }, [contacts]);
  useEffect(() => { selectedSessionRef.current = selectedSessionId; }, [selectedSessionId]);

  // ── Shared UI state ─────────────────────────────────────────────────────
  const [activeTab, setActiveTab] = useState<ActiveTab>("estado");
  const [toasts, setToasts]       = useState<Toast[]>([]);

  // ── Modal state (Encerrar triggered from ActionBar) ────────────────────────
  const [showCloseModal, setShowCloseModal] = useState(false);

  // Dedup guards
  const notifiedAssignments   = useRef<Set<string>>(new Set());
  const pendingClosedSessions = useRef<Map<string, string>>(new Map());
  const handledSessions       = useRef<Set<string>>(new Set());

  // ── Supervisor hooks — scoped to selected contact ───────────────────────
  // Cast away the _pool_id tag for hooks that expect WsServerEvent | null
  const lastWsEvent = lastEvent as import("./types").WsServerEvent | null;
  const supervisorState = useSupervisorState(selectedSessionId, lastWsEvent);
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
        // Update slaTargetMs from supervisorState when available
        slaTargetMs:
          supervisorState?.sla?.target_ms ?? c.slaTargetMs,
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
      // non-fatal
    }
  }, []);

  // ── WS event handler ────────────────────────────────────────────────────
  useEffect(() => {
    if (!lastEvent) return;
    const sourcePoolId = lastEvent._pool_id;
    // Re-typed as WsServerEvent for discriminated union narrowing
    const event = lastEvent as import("./types").WsServerEvent;

    if (event.type === "connection.accepted") return;

    // ── New contact assigned ────────────────────────────────────────────
    if (event.type === "conversation.assigned") {
      const { session_id, contact_id, pool_id } = event;
      const resolvedPool = pool_id ?? sourcePoolId;

      if (handledSessions.current.has(session_id)) return;

      const isNew = !contactsRef.current.has(session_id) &&
                    !notifiedAssignments.current.has(session_id);
      notifiedAssignments.current.add(session_id);

      // Lookup slaTargetMs from available pools
      const poolInfo = availablePools.find(p => p.pool_id === resolvedPool);
      const slaTargetMs = poolInfo?.sla_target_ms ?? null;

      setContacts(prev => {
        if (prev.has(session_id)) return prev;
        const next = new Map(prev);
        const alreadyClosed = pendingClosedSessions.current.has(session_id);
        pendingClosedSessions.current.delete(session_id);
        next.set(session_id, {
          ...makeContact(session_id, resolvedPool),
          contactId:         contact_id ?? null,
          slaTargetMs,
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

  // ── Derived state ───────────────────────────────────────────────────────
  const selected   = selectedSessionId ? contacts.get(selectedSessionId) ?? null : null;
  const wsStatus   = aggregateStatus(statuses, activePools);

  // Show the selected contact's pool in the header, or the first active pool
  const headerPoolId = selected?.poolId ?? activePools[0] ?? "";

  // ── Main render ─────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full overflow-hidden bg-gray-50">
      <Header
        agentName={agentName}
        poolId={headerPoolId}
        sessionId={selectedSessionId}
        wsStatus={wsStatus}
        sla={selected?.supervisorState?.sla ?? null}
        sessionStartedAt={selected?.sessionStartedAt ?? null}
        contactCount={contacts.size}
        pools={availablePools}
        activePools={activePools}
        poolStatuses={statuses}
        onTogglePool={handleTogglePool}
        onJoinAll={handleJoinAll}
      />

      <div className="flex flex-1 overflow-hidden">

        {/* Contact list — gray column, no explicit right border (tab bleed handles separator) */}
        <div className="w-[200px] bg-gray-100 overflow-hidden flex flex-col flex-shrink-0">
          <ContactList
            contacts={[...contacts.values()]}
            selectedSessionId={selectedSessionId}
            aiTypingSessions={aiTypingSessions}
            onSelect={handleSelectContact}
          />
        </div>

        {/* White surface: chat + right panel share a unified white background */}
        <div className="flex flex-1 overflow-hidden bg-white">

          {/* Chat column */}
          <div className="flex flex-col flex-1 overflow-hidden">

            {/* Action bar — contact identity lives ONLY here */}
            <ActionBar
              contact={selected}
              onEncerrar={() => setShowCloseModal(true)}
              onPausar={() => addToast("Pausar: em breve", "info")}
              onTransferir={() => addToast("Transferir: em breve", "info")}
              onDesligar={() => addToast("Desligar: em breve", "info")}
            />

            {/* Chat area or idle placeholder */}
            {!selected ? (
              <div className="flex-1 flex flex-col items-center justify-center text-gray-400 text-sm select-none gap-3">
                {activePools.length === 0 ? (
                  <>
                    <span className="text-3xl">🟢</span>
                    <p className="text-center leading-snug max-w-xs">
                      Ative um pool no cabeçalho para ficar disponível.
                    </p>
                  </>
                ) : (
                  <>
                    <span className="text-3xl animate-pulse">⏳</span>
                    <p>Aguardando próximo atendimento…</p>
                  </>
                )}
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
              disabled={!selected}
              sessionClosed={selected?.sessionClosed ?? false}
              capabilities={selected?.capabilities ?? null}
            />
          </div>

          {/* Right panel — fixed 280px, shares white bg with chat */}
          <div className="w-[280px] overflow-hidden flex flex-col flex-shrink-0 border-l border-gray-200">
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
      </div>

      {/* Close modal — triggered by ActionBar's Encerrar or auto-shown on client_disconnect */}
      {(showCloseModal || selected?.pendingCloseModal) && selected && (
        <CloseModal
          defaultIssueStatus={selected.sessionClosed ? "Cliente desconectou" : ""}
          defaultOutcome={selected.sessionClosed ? "abandoned" : "resolved"}
          onConfirm={(payload) => {
            setShowCloseModal(false);
            handleClose(selected.sessionId, payload);
          }}
          onCancel={() => {
            setShowCloseModal(false);
            if (selected.sessionClosed) {
              handleClose(selected.sessionId, {
                issue_status: "Cliente desconectou",
                outcome: "abandoned",
              });
            }
          }}
        />
      )}

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
};
