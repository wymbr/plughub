/**
 * App — Multi-contact Agent Assist UI
 *
 * Layout:
 *   ┌──────────┬───────────────────────┬──────────────────┐
 *   │ Header (full width, top)                            │
 *   ├──────────┬───────────────────────┬──────────────────┤
 *   │ Contact  │  Chat Area            │  Right Panel     │
 *   │ List     │  (selected contact)   │  (context)       │
 *   │  (20%)   │       (50%)           │       (30%)      │
 *   ├──────────┴───────────────────────┴──────────────────┤
 *   │  AgentInput  (tied to selected contact)             │
 *   └─────────────────────────────────────────────────────┘
 *
 * State model:
 *   - contacts: Map<sessionId, ContactSession> — all active contacts
 *   - selectedSessionId: string | null — which contact the agent is viewing
 *   - One WebSocket connection for all contacts (useAgentWebSocket)
 *   - Events are routed to the correct ContactSession by session_id in the payload
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActiveTab,
  ChatMessage,
  ClosePayload,
  ContactSession,
  Toast,
  WsServerEvent,
} from "./types";
import { useAgentWebSocket }     from "./hooks/useAgentWebSocket";
import { useSupervisorState }    from "./hooks/useSupervisorState";
import { useSupervisorCapabilities } from "./hooks/useSupervisorCapabilities";
import { Header }          from "./components/Header";
import { ChatArea }        from "./components/ChatArea";
import { AgentInput }      from "./components/AgentInput";
import { CloseModal }      from "./components/CloseModal";
import { RightPanel }      from "./components/RightPanel";
import { ContactList }     from "./components/ContactList";
import { ToastContainer }  from "./components/ToastContainer";

interface AppProps {
  agentName: string;
  poolId:    string;
}

let toastSeq = 0;
function makeToastId(): string { return `toast-${++toastSeq}`; }

function makeContact(sessionId: string, channel = "webchat"): ContactSession {
  return {
    sessionId,
    contactId:        null,
    customerName:     null,
    channel,
    messages:         [],
    supervisorState:  null,
    capabilities:     null,
    sessionStartedAt: new Date(),
    unreadCount:      0,
    sessionClosed:    false,
    pendingCloseModal: false,
  };
}

const App: React.FC<AppProps> = ({ agentName, poolId }) => {
  // ── Multi-contact state ───────────────────────────────────────────────────
  const [contacts, setContacts] = useState<Map<string, ContactSession>>(new Map());
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

  // Refs that always hold the latest value — used inside WS event handler
  // to avoid stale closure bugs (the handler's dep array omits these on purpose
  // so it doesn't re-create on every contact / selection change).
  const contactsRef         = useRef<Map<string, ContactSession>>(new Map());
  const selectedSessionRef  = useRef<string | null>(null);
  useEffect(() => { contactsRef.current        = contacts;        }, [contacts]);
  useEffect(() => { selectedSessionRef.current = selectedSessionId; }, [selectedSessionId]);

  // ── Shared UI state ───────────────────────────────────────────────────────
  const [activeTab, setActiveTab] = useState<ActiveTab>("estado");
  const [toasts, setToasts]       = useState<Toast[]>([]);

  // ── WebSocket — one connection for all contacts ───────────────────────────
  const { status: wsStatus, send, lastEvent } = useAgentWebSocket(poolId || null);

  // Track sessions for which we've already shown "Novo contato atribuído" so
  // repeated conversation.assigned events (routing drain) don't spam toasts.
  const notifiedAssignments = useRef<Set<string>>(new Set());

  // Pending closed sessions: session.closed may arrive before conversation.assigned
  // (routing drain re-emits assigned after the close event). We hold the close
  // reason here and apply it when the contact is eventually added to the Map.
  const pendingClosedSessions = useRef<Map<string, string>>(new Map());

  // Sessions that the agent has already handled (agent_done submitted).
  // Any conversation.assigned arriving after handleClose must be silently ignored
  // — the routing engine's periodic drain re-emits routed events for dead sessions
  // and would otherwise resurrect the contact in the sidebar.
  const handledSessions = useRef<Set<string>>(new Set());

  // ── Supervisor hooks — scoped to selected contact ─────────────────────────
  const supervisorState = useSupervisorState(selectedSessionId, lastEvent);
  const capabilities    = useSupervisorCapabilities(selectedSessionId, supervisorState);

  // Sync supervisor data back into the selected contact's state.
  // Also extracts customerName from issue_status context when first available —
  // real name resolution would come from CRM (future); for now we keep null
  // and let ContactList fall back to the session ID short form.
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
        // Preserve any name already resolved; null stays null until CRM integration
        customerName:    c.customerName,
      });
      return next;
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [supervisorState, capabilities]);

  // AI typing timer per session
  const aiTypingTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const [aiTypingSessions, setAiTypingSessions] = useState<Set<string>>(new Set());

  // ── Toast helpers ─────────────────────────────────────────────────────────
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

  // ── History loader ────────────────────────────────────────────────────────
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

  // ── WS event handler ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!lastEvent) return;
    const event: WsServerEvent = lastEvent;

    if (event.type === "connection.accepted") {
      // Status já visível no header (● Connected) — sem toast para não roubar foco
      return;
    }

    // ── New contact assigned ──────────────────────────────────────────────
    if (event.type === "conversation.assigned") {
      const { session_id, contact_id } = event;

      // Reject sessions already handled by the agent — routing drain re-emits
      // conversation.assigned for dead sessions long after agent_done.
      if (handledSessions.current.has(session_id)) return;

      // Only process if truly new — dedup against both Map state (via ref) and
      // our own notification set (handles repeated routing drain events).
      const isNew = !contactsRef.current.has(session_id) &&
                    !notifiedAssignments.current.has(session_id);
      notifiedAssignments.current.add(session_id);

      setContacts(prev => {
        if (prev.has(session_id)) return prev;  // already tracked (reconnect)
        const next = new Map(prev);
        // Check if a session.closed already arrived before this assigned event
        const alreadyClosed = pendingClosedSessions.current.has(session_id);
        pendingClosedSessions.current.delete(session_id);
        next.set(session_id, {
          ...makeContact(session_id),
          contactId:        contact_id ?? null,
          sessionClosed:    alreadyClosed,
          pendingCloseModal: alreadyClosed,
        });
        return next;
      });
      if (!isNew) return;  // duplicate event — skip side-effects
      // Auto-select if no contact is currently selected (first contact)
      setSelectedSessionId(prev => prev ?? session_id);
      fetchHistory(session_id);
      // Update URL with the first session only (for single-tab browser refresh compat)
      const u = new URL(window.location.href);
      if (!u.searchParams.get("session_id")) {
        u.searchParams.set("session_id", session_id);
        window.history.replaceState({}, "", u.toString());
      }
      addToast("Novo contato atribuído", "info");
      return;
    }

    // ── Incoming message ──────────────────────────────────────────────────
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
        if (c.messages.some(m => m.id === msg.id)) return prev;  // deduplicate
        // Use ref — selectedSessionId in the closure may be stale
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

    // ── AI typing indicator ───────────────────────────────────────────────
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

    // ── Session closed ────────────────────────────────────────────────────
    if (event.type === "session.closed") {
      const sid = (event as unknown as Record<string, unknown>)["session_id"] as string | undefined;
      if (!sid) return;

      if (event.reason === "client_disconnect") {
        // Save in pending map in case conversation.assigned hasn't arrived yet.
        pendingClosedSessions.current.set(sid, event.reason);

        setContacts(prev => {
          const c = prev.get(sid);
          if (!c) return prev;  // contact not yet in map — will be applied on assigned
          const next = new Map(prev);
          next.set(sid, { ...c, sessionClosed: true, pendingCloseModal: true });
          return next;
        });
        // Only show toast once per session
        if (!notifiedAssignments.current.has(`closed:${sid}`)) {
          notifiedAssignments.current.add(`closed:${sid}`);
          addToast("Cliente desconectou. Preencha o encerramento.", "warning", true);
        }
      } else {
        // Server-initiated close — remove contact from map
        pendingClosedSessions.current.delete(sid);
        setContacts(prev => {
          const next = new Map(prev);
          next.delete(sid);
          return next;
        });
        // If this was selected, pick another or go to lobby.
        // Use contactsRef — contacts in the closure may be stale.
        setSelectedSessionId(prev => {
          if (prev !== sid) return prev;
          const remaining = [...contactsRef.current.keys()].filter(k => k !== sid);
          return remaining[0] ?? null;
        });
        const u = new URL(window.location.href);
        u.searchParams.delete("session_id");
        window.history.replaceState({}, "", u.toString());
      }
      return;
    }

    // ── Menu render ───────────────────────────────────────────────────────
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

    // ── @mention command acknowledgement ──────────────────────────────────
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

  // Mark messages as read and reset tab when switching to a contact
  const handleSelectContact = useCallback((sessionId: string) => {
    setSelectedSessionId(sessionId);
    setActiveTab("estado");   // always land on Estado tab for fresh context
    setContacts(prev => {
      const c = prev.get(sessionId);
      if (!c || c.unreadCount === 0) return prev;
      const next = new Map(prev);
      next.set(sessionId, { ...c, unreadCount: 0 });
      return next;
    });
  }, []);

  // ── Send handler ──────────────────────────────────────────────────────────
  const handleSend = useCallback(
    (text: string) => {
      if (!selectedSessionId) return;
      send(text, selectedSessionId);
      // @mention messages are agents_only — mark them amber so they stand out
      // from regular customer-visible messages. The server echoes them back via
      // agent:events but without session_id (so the echo is dropped by the
      // message.text handler). The optimistic bubble is the authoritative copy.
      const isMention = text.trimStart().startsWith("@");
      // Optimistic local message
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

  // ── Close session handler ─────────────────────────────────────────────────
  const handleClose = useCallback(
    async (sessionId: string, payload: ClosePayload) => {
      // Mark immediately — prevents routing drain from re-adding this session
      // even if the API call below is slow or fails.
      handledSessions.current.add(sessionId);
      try {
        await fetch(`/api/agent_done/${sessionId}`, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify(payload),
        });
        // Remove contact from map and switch selection
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
        const u = new URL(window.location.href);
        if (u.searchParams.get("session_id") === sessionId) {
          u.searchParams.delete("session_id");
          window.history.replaceState({}, "", u.toString());
        }
        addToast("Atendimento encerrado.", "info");
      } catch {
        addToast("Erro ao encerrar atendimento.", "error");
      }
    },
    [addToast]
  );

  // ── Escalate / invite stubs ───────────────────────────────────────────────
  const handleInviteAgent = useCallback(
    (agentTypeId: string) => addToast(`Convite enviado: ${agentTypeId}`, "info"),
    [addToast]
  );
  const handleEscalate = useCallback(
    (targetPoolId: string) => addToast(`Escalando para: ${targetPoolId}`, "warning"),
    [addToast]
  );

  // ── Selected contact snapshot ─────────────────────────────────────────────
  const selected = selectedSessionId ? contacts.get(selectedSessionId) ?? null : null;

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-screen overflow-hidden bg-gray-50">
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
        <div className="w-[20%] border-r border-gray-200 overflow-y-auto">
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
        <div className="w-[30%] overflow-hidden">
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

      {/* Close modal — shown for selected contact when customer disconnected */}
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

export default App;
