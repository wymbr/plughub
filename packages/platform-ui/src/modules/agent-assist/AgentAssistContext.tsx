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
    const json = await res.json() as
      | { pools: Array<{ pool_id: string; display_name?: string; channel_types?: string[]; sla_target_ms?: number | null }> }
      | Array<{ pool_id: string; display_name?: string; channel_types?: string[]; sla_target_ms?: number | null }>;
    const data = Array.isArray(json) ? json : (json.pools ?? []);
    const list: PoolInfo[] = data.map(p => ({
      pool_id:       p.pool_id,
      display_name:  p.display_name,
      channel_types: p.channel_types ?? [],
      sla_target_ms: p.sla_target_ms ?? null,
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
  send:       (text: string, sessionId: string) => void;

  // Contacts (critical to persist)
  contacts:            Map<string, ContactSession>;
  setContacts:         React.Dispatch<React.SetStateAction<Map<string, ContactSession>>>;
  selectedSessionId:   string | null;
  setSelectedSessionId:(id: string | null) => void;

  // AI typing
  aiTypingSessions: Set<string>;

  // Toasts
  toasts:       Toast[];
  addToast:     (message: string, type?: Toast["type"], persistent?: boolean) => void;
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
  const { statuses, lastEvent, send } = useMultiPoolWebSocket(activePools);

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

  // ── AI typing ─────────────────────────────────────────────────────────────
  const aiTypingTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const [aiTypingSessions, setAiTypingSessions] = useState<Set<string>>(new Set());

  // ── Dedup refs ────────────────────────────────────────────────────────────
  const notifiedAssignments   = useRef<Set<string>>(new Set());
  const pendingClosedSessions = useRef<Map<string, string>>(new Map());
  const handledSessions       = useRef<Set<string>>(new Set());

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
      const { session_id, contact_id, pool_id } = event;
      const resolvedPool = pool_id ?? sourcePoolId;

      if (handledSessions.current.has(session_id)) return;

      const isNew = !contactsRef.current.has(session_id) &&
                    !notifiedAssignments.current.has(session_id);
      notifiedAssignments.current.add(session_id);

      const poolInfo   = availablePools.find(p => p.pool_id === resolvedPool);
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

      // Remove the contact — all hooks have completed.
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
      if (event.reason === "client_disconnect") {
        addToast("Cliente desconectou. Atendimento encerrado.", "warning");
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
