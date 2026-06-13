/**
 * AgentAssistContext
 *
 * Holds all WebSocket connections and contact state at the Shell level so they
 * survive navigation. AgentAssistPage reads from this context and renders the UI;
 * when the user navigates away and back, every open WS and every contact in
 * progress is exactly where they left off.
 *
 * State owned here (must persist across navigation):
 *   - availablePools       registry fetch result
 *   - activePools          set of pool_ids the agent is "Ready" in
 *   - contacts             Map<sessionId, ContactSession> — THE critical state
 *   - selectedSessionId    which contact is focused
 *   - aiTypingSessions     Set<sessionId> for typing indicators
 *   - toasts               global notification queue
 *   - WS connections       via useMultiPoolWebSocket
 *   - all refs             dedup guards, timers, etc.
 *
 * State NOT owned here (UI-local, fine to reset on navigation):
 *   - activeTab, showCloseModal, substitutionMode, lastCopilotEvent
 *   - supervisorState, capabilities, copilotSuggestions (per-selected-session hooks)
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { useAuth } from "@/auth/useAuth";
import {
  ChatMessage,
  ContactSession,
  PoolConnectionStatus,
  PoolInfo,
  ResponseTimer,
  Toast,
  WsStatus,
} from "./types";
import { useMultiPoolWebSocket } from "./hooks/useMultiPoolWebSocket";
import type { TaggedWsEvent }    from "./hooks/useMultiPoolWebSocket";

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
    instanceId:        null,
    slaTargetMs:       null,
    maxReplyTimeMs:    null,
    messages:          [],
    supervisorState:   null,
    capabilities:      null,
    sessionStartedAt:  new Date(),
    unreadCount:       0,
    sessionClosed:     false,
    pendingCloseModal: false,
    // Start counting immediately — agent is obligated to initiate regardless of
    // who sent the first message (rule a from spec).
    responseTimer:     { status: 'counting', startedAt: Date.now() } satisfies ResponseTimer,
  };
}

// ── Aggregate WS status helper ─────────────────────────────────────────────
export function aggregateStatus(
  statuses: Map<string, PoolConnectionStatus>,
  activePools: string[],
): WsStatus {
  if (activePools.length === 0) return "disconnected";
  const vals = activePools.map(p => statuses.get(p) ?? "disconnected");
  if (vals.some(v => v === "connected"))  return "connected";
  if (vals.some(v => v === "connecting")) return "connecting";
  return "disconnected";
}

// ── Fetch pools from agent-registry ───────────────────────────────────────
async function fetchPools(accessiblePools: string[], accessToken?: string): Promise<PoolInfo[]> {
  try {
    const headers: Record<string, string> = {
      "x-tenant-id": "tenant_demo",
      "x-user-id":   "operator",
    };
    if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
    const res  = await fetch(`${API_BASE}/pools`, { headers });
    if (!res.ok) return [];
    type RawPool = { pool_id: string; display_name?: string; channel_types?: string[]; sla_target_ms?: number | null; max_reply_time_ms?: number | null };
    const json = await res.json() as { pools: RawPool[] } | RawPool[];
    const data = Array.isArray(json) ? json : (json.pools ?? []);
    const list: PoolInfo[] = data.map(p => ({
      pool_id:               p.pool_id,
      display_name:          p.display_name,
      channel_types:         p.channel_types ?? [],
      sla_target_ms:         p.sla_target_ms ?? null,
      max_reply_time_ms:     p.max_reply_time_ms ?? null,
    }));
    if (accessiblePools.length === 0) return list;
    return list.filter(p => accessiblePools.includes(p.pool_id));
  } catch {
    return [];
  }
}

// ── Context value type ─────────────────────────────────────────────────────
export interface AgentAssistContextValue {
  // Pools
  availablePools:    PoolInfo[];
  activePools:       string[];
  handleTogglePool:  (poolId: string) => void;
  handleJoinAll:     () => void;
  handleLeaveAll:    () => void;

  // WS
  statuses:   Map<string, PoolConnectionStatus>;
  lastEvent:  TaggedWsEvent | null;
  send:       (text: string, sessionId: string, visibility?: string) => void;

  // Contacts (critical to persist)
  contacts:            Map<string, ContactSession>;
  setContacts:         React.Dispatch<React.SetStateAction<Map<string, ContactSession>>>;
  selectedSessionId:   string | null;
  setSelectedSessionId:(id: string | null) => void;

  // AI typing
  aiTypingSessions: Set<string>;

  // Toasts
  toasts:       Toast[];
  addToast:     (message: string, type?: Toast["type"], persistent?: boolean) => string;
  dismissToast: (id: string) => void;

  // History
  fetchHistory: (sessionId: string) => Promise<void>;

  // Dedup guard exposed so AgentAssistPage can add to it
  handledSessions: React.MutableRefObject<Set<string>>;
}

const AgentAssistContext = createContext<AgentAssistContextValue | null>(null);

export function useAgentAssist(): AgentAssistContextValue {
  const ctx = useContext(AgentAssistContext);
  if (!ctx) throw new Error("useAgentAssist must be used inside AgentAssistProvider");
  return ctx;
}

// ── Provider ───────────────────────────────────────────────────────────────
export const AgentAssistProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { session } = useAuth();
  const accessiblePools: string[] = session?.accessiblePools ?? [];

  // ── Available pools (from registry) ──────────────────────────────────────
  const [availablePools, setAvailablePools] = useState<PoolInfo[]>([]);
  useEffect(() => {
    fetchPools(accessiblePools, session?.accessToken).then(setAvailablePools);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Presence ──────────────────────────────────────────────────────────────
  const [activePools, setActivePools] = useState<string[]>([]);

  const handleTogglePool = useCallback((poolId: string) => {
    setActivePools(prev =>
      prev.includes(poolId) ? prev.filter(p => p !== poolId) : [...prev, poolId]
    );
  }, []);

  const handleJoinAll = useCallback(() => {
    setActivePools(availablePools.map(p => p.pool_id));
  }, [availablePools]);

  const handleLeaveAll = useCallback(() => {
    setActivePools([]);
  }, []);

  // ── Multi-pool WebSocket ──────────────────────────────────────────────────
  // Pass user identity so the server keys the Redis instance per-user rather
  // than per-pool, enabling shared capacity across all logged-in pools.
  const wsUserId       = session?.userId ?? ""
  const wsMaxConcurrent = session?.maxConcurrentSessions ?? 3
  const wsUserLogin    = session?.email ?? ""   // C1: denormalized onto the human segment
  const { statuses, lastEvent, send, registerSession, unregisterSession } = useMultiPoolWebSocket(
    activePools, wsUserId, wsMaxConcurrent, wsUserLogin
  );

  // ── Multi-contact state ───────────────────────────────────────────────────
  const [contacts, setContacts]           = useState<Map<string, ContactSession>>(new Map());
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

  const contactsRef        = useRef<Map<string, ContactSession>>(new Map());
  const selectedSessionRef = useRef<string | null>(null);
  useEffect(() => { contactsRef.current        = contacts;         }, [contacts]);
  useEffect(() => { selectedSessionRef.current = selectedSessionId; }, [selectedSessionId]);

  // ── Toasts ────────────────────────────────────────────────────────────────
  const [toasts, setToasts] = useState<Toast[]>([]);

  const addToast = useCallback(
    (message: string, type: Toast["type"] = "info", persistent = false): string => {
      const id = makeToastId();
      setToasts(prev => [...prev, { id, message, type, persistent }]);
      if (!persistent) {
        setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 5000);
      }
      return id;
    },
    []
  );

  const dismissToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  // Item 2 (capacity-governance): toast de login negado por pool — dedupe.
  const loginDeniedToasts = useRef<Map<string, string>>(new Map());

  // ── AI typing ─────────────────────────────────────────────────────────────
  const aiTypingTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const [aiTypingSessions, setAiTypingSessions] = useState<Set<string>>(new Set());

  // ── Dedup refs ────────────────────────────────────────────────────────────
  const notifiedAssignments   = useRef<Set<string>>(new Set());
  const pendingClosedSessions = useRef<Map<string, string>>(new Map());
  const handledSessions       = useRef<Set<string>>(new Set());
  // Tracks the persistent "Aguardando encerramento..." toast ID per session so
  // it can be dismissed when session.closed reason="agent_done" arrives.
  const disconnectToastIds    = useRef<Map<string, string>>(new Map());

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
      // non-fatal
    }
  }, []);

  // ── WS event handler ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!lastEvent) return;
    const sourcePoolId = lastEvent._pool_id;
    const event = lastEvent as import("./types").WsServerEvent;

    if (event.type === "connection.accepted") return;

    // ── New contact assigned ──────────────────────────────────────────────
    if (event.type === "conversation.assigned") {
      const { session_id, contact_id, pool_id, instance_id } = event;
      const resolvedPool = pool_id ?? sourcePoolId;

      // Register session→pool mapping so send() targets the correct WS connection
      registerSession(session_id, resolvedPool);

      if (handledSessions.current.has(session_id)) return;

      const isNew = !contactsRef.current.has(session_id) &&
                    !notifiedAssignments.current.has(session_id);
      notifiedAssignments.current.add(session_id);

      const poolInfo      = availablePools.find(p => p.pool_id === resolvedPool);
      const slaTargetMs   = poolInfo?.sla_target_ms    ?? null;
      const maxReplyTimeMs = poolInfo?.max_reply_time_ms ?? null;

      setContacts(prev => {
        if (prev.has(session_id)) return prev;
        const next = new Map(prev);
        const alreadyClosed = pendingClosedSessions.current.has(session_id);
        pendingClosedSessions.current.delete(session_id);
        next.set(session_id, {
          ...makeContact(session_id, resolvedPool),
          contactId:         contact_id ?? null,
          instanceId:        instance_id ?? null,
          slaTargetMs,
          maxReplyTimeMs,
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
        if (c.messages.some(m => m.id === msg.id)) return prev;
        // After sessionClosed, customer messages are NPS/hook responses directed
        // at the hook agents (not the human agent). Suppress them so the agent
        // doesn't see the customer's NPS answer in their closed-session view.
        if (c.sessionClosed && msg.author === "customer") return prev;
        const isSelected = sid === selectedSessionRef.current;

        // ── Response timer state machine ────────────────────────────────────
        // counting → agent owes a reply (live orange/red counter)
        // frozen   → agent replied; green display of how long the reply took
        //
        // Rules:
        //   Customer msg + counting → no change (keep original startedAt)
        //   Customer msg + frozen   → reset to counting(now)
        //   Agent reply  + counting → freeze(now - startedAt)
        //   Agent reply  + frozen   → no change
        const isCustomerMsg = msg.author === "customer" &&
          (msg.visibility === "all" || msg.visibility === undefined || msg.visibility === null);
        const isAgentReply =
          msg.author === "agent_human" ||
          (msg.author === "agent_ai" &&
            (msg.visibility === "all" || msg.visibility === undefined || msg.visibility === null));

        let responseTimer = c.responseTimer;
        if (isCustomerMsg && responseTimer.status === 'frozen') {
          // rule: customer msg + frozen → zero and restart
          responseTimer = { status: 'counting', startedAt: Date.now() };
        } else if (isAgentReply && responseTimer.status === 'counting') {
          // rule: agent reply + counting → freeze with elapsed time
          responseTimer = { status: 'frozen', elapsedMs: Date.now() - responseTimer.startedAt };
        }
        // all other combinations → no change

        const next = new Map(prev);
        next.set(sid, {
          ...c,
          messages:      [...c.messages, msg],
          unreadCount:   isSelected ? 0 : c.unreadCount + 1,
          responseTimer,
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

    // ── Login negado (capacity-governance item 2 — gates por tipo) ────────
    // O mcp-server envia `login_denied` e fecha o WS quando o gate recusa:
    // C_human esgotado (logins concorrentes) ou pool de agentes IA.
    // Dedupe: UM toast por pool (substitui o anterior em vez de empilhar —
    // o hook já suprime o reconnect, isto cobre re-tentativas manuais).
    if ((event as unknown as Record<string, unknown>)["type"] === "login_denied") {
      const e = event as unknown as Record<string, unknown>;
      const poolKey = String(e["_pool_id"] ?? e["pool_id"] ?? "");
      const msg =
        e["reason"] === "human_capacity_exhausted"
          ? `Login negado: limite contratado de agentes humanos concorrentes atingido (${e["current"]}/${e["limit"]}). Aguarde um colega sair ou contate o administrador.`
          : e["reason"] === "pool_kind_mismatch"
          ? `Login negado: o pool "${e["pool_id"]}" é de agentes IA — login humano não é permitido.`
          : "Login negado pela governança de capacidade.";
      const prevId = loginDeniedToasts.current.get(poolKey);
      if (prevId) dismissToast(prevId);
      loginDeniedToasts.current.set(poolKey, addToast(msg, "error", /* persistent */ true));
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

    // ── Agent done (wrapping up) ─────────────────────────────────────────
    // The human part is done but on_human_end hooks (wrapup, NPS) may still
    // be active.  Mark the contact as wrapping-up without removing it so the
    // agent can still see and respond to hook agent messages.
    if (event.type === "session.agent_done") {
      return;  // no-op — the contact stays; handleClose already set sessionClosed
    }

    // ── Session closed ────────────────────────────────────────────────────
    if (event.type === "session.closed") {
      const sid = (event as unknown as Record<string, unknown>)["session_id"] as string | undefined;
      if (!sid) return;

      // "client_disconnect" means the CUSTOMER disconnected but the bridge may
      // still fire on_human_end hooks (wrapup, NPS) for the agent.  Do NOT remove
      // the contact yet — mark it as sessionClosed so the agent sees the correct
      // state and can still interact with hook agents.  The actual removal happens
      // when reason === "agent_done" (published by _trigger_contact_close after all
      // hooks have completed).
      // G7 Slice B: "agent_transfer" — a origem transferiu o contato (o contato
      // SEGUE pelo destino). Não remover: entrar em modo wrap-up para a origem
      // responder o wrap-up do seu segmento (segment_wrapup). A remoção acontece
      // quando o wrap-up conclui (session.closed reason="posatt_segment_complete"
      // com a origem nos recipients).
      if (
        event.reason === "client_disconnect" ||
        event.reason === "customer_disconnect" ||
        event.reason === "session_timeout" ||
        event.reason === "timeout" ||
        event.reason === "agent_transfer"
      ) {
        const reasonLabel =
          event.reason === "agent_transfer"
            ? "Contato transferido."
            : event.reason === "session_timeout" || event.reason === "timeout"
            ? "Sessão encerrada por inatividade."
            : "Cliente desconectou.";
        const toastId = addToast(`${reasonLabel} Aguardando encerramento...`, "warning", /* persistent */ true);
        disconnectToastIds.current.set(sid, toastId);
        setContacts(prev => {
          const c = prev.get(sid);
          if (!c) return prev;
          const next = new Map(prev);
          // Mark as closed (disables input, shows indicator) but do NOT open the
          // CloseModal — the bridge is running on_human_end hooks and will send
          // session.closed reason="agent_done" when they finish, at which point
          // the contact is removed automatically.
          next.set(sid, { ...c, sessionClosed: true, pendingCloseModal: false });
          return next;
        });
        return;
      }

      // For "agent_done" (and any other reason): all hooks have completed —
      // clean up session→pool mapping and remove the contact.
      // Dismiss the persistent "Aguardando encerramento..." toast if it was shown
      // when the customer disconnected earlier.
      const pendingToastId = disconnectToastIds.current.get(sid);
      if (pendingToastId) {
        dismissToast(pendingToastId);
        disconnectToastIds.current.delete(sid);
      }
      unregisterSession(sid);
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
      return;
    }

    // ── Menu render ───────────────────────────────────────────────────────
    if (event.type === "menu.render") {
      const sid = (event as unknown as Record<string, unknown>)["session_id"] as string | undefined;
      if (!sid) return;
      // Detect if the menu targets the current agent (not the customer).
      // Array visibility or "agents_only" means the agent IS the respondent —
      // MenuCard should be interactive without needing the global substitution toggle.
      const vis = (event as unknown as Record<string, unknown>)["visibility"];
      const targetsSelf = Array.isArray(vis) || vis === "agents_only";
      const menuMsg: ChatMessage = {
        id:        `menu-${event.menu_id}`,
        author:    "system",
        text:      event.prompt,
        timestamp: new Date().toISOString(),
        menuData: {
          menu_id:      event.menu_id,
          source_instance: (event as unknown as Record<string, unknown>)["source_instance"] as string | undefined,
          interaction:  event.interaction,
          prompt:       event.prompt,
          options:      event.options,
          fields:       event.fields,
          targetsSelf,
          masked_fields: (event.masked_fields as string[] | null | undefined)
            ?.filter((f): f is string => typeof f === "string") ?? undefined,
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
  }, [lastEvent, addToast, dismissToast, fetchHistory, registerSession, unregisterSession]);

  // Clear typing timers on unmount (full app unmount, not navigation)
  useEffect(() => {
    return () => {
      for (const t of aiTypingTimers.current.values()) clearTimeout(t);
    };
  }, []);

  const value: AgentAssistContextValue = {
    availablePools,
    activePools,
    handleTogglePool,
    handleJoinAll,
    handleLeaveAll,
    statuses,
    lastEvent,
    send,
    contacts,
    setContacts,
    selectedSessionId,
    setSelectedSessionId,
    aiTypingSessions,
    toasts,
    addToast,
    dismissToast,
    fetchHistory,
    handledSessions,
  };


  return (
    <AgentAssistContext.Provider value={value}>
      {children}
    </AgentAssistContext.Provider>
  );
};
