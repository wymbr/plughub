/**
 * JourneyPanel — Arc 11 Fase 2 (Fase E)
 *
 * Center-area "Journey" tab content. Shows:
 *   - The journey this contact belongs to (matched via origin_session_id or
 *     the most recent active/suspended journey for the customer).
 *   - A session list: origin session + customer history sessions created after
 *     journey.created_at + current active session (always last).
 *   - A read-only transcript viewer for the selected session.
 *
 * If the customer is standalone (no matching journey), renders a placeholder.
 * If customer is unknown, renders a prompt to identify the customer first.
 *
 * Data sources (no new backend endpoints required):
 *   GET /analytics/reports/journeys?customer_id=X&tenant_id=T  — journey list
 *   GET /analytics/sessions/customer/:customerId?tenant_id=T    — session history
 *   GET /api/conversation_history/:sessionId                    — transcript
 */

import React, { useEffect, useRef, useState } from "react";
import { Timer } from "lucide-react";
import type { Journey, JourneyStatus } from "@/modules/workflows/api/hooks";
import { ContactHistoryEntry } from "../types";

const ANALYTICS_BASE = import.meta.env.VITE_ANALYTICS_URL ?? "/analytics";
const TENANT_ID      = import.meta.env.VITE_TENANT_ID ?? "tenant_demo";

// ── Types ─────────────────────────────────────────────────────────────────────

interface SessionRow {
  sessionId:   string;
  openedAt:    string | null;
  poolId:      string;
  outcome:     string | null;
  durationMs:  number | null;
  isCurrent:   boolean;
  isOrigin:    boolean;
}

interface JourneyPanelProps {
  customerId?:        string | null;
  tenantId?:          string | null;
  currentSessionId:   string;
}

// ── Status config ─────────────────────────────────────────────────────────────

const STATUS_ICON: Record<JourneyStatus, string> = {
  active:    "🔵",
  suspended: "⏸️",
  completed: "✅",
  failed:    "❌",
  cancelled: "🚫",
};

const STATUS_LABEL: Record<JourneyStatus, string> = {
  active:    "Ativo",
  suspended: "Suspenso",
  completed: "Concluído",
  failed:    "Falhou",
  cancelled: "Cancelado",
};

const OUTCOME_ICON: Record<string, string> = {
  resolved:  "✅",
  escalated: "⬆️",
  abandoned: "⚠️",
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("pt-BR", {
      day: "2-digit", month: "2-digit",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

function formatDuration(ms: number | null): string {
  if (!ms) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

function skillLabel(skillId: string): string {
  return skillId.replace(/^skill_/, "").replace(/_v\d+$/, "").replace(/_/g, " ");
}

function poolLabel(poolId: string): string {
  return poolId.replace(/_/g, " ");
}

// ── Data hooks ────────────────────────────────────────────────────────────────

function useCustomerJourneys(tenantId: string | null | undefined, customerId: string | null) {
  const [journeys, setJourneys] = useState<Journey[]>([]);
  const [loading,  setLoading]  = useState(false);

  useEffect(() => {
    if (!tenantId || !customerId) { setJourneys([]); return; }
    let active = true;
    setLoading(true);
    const params = new URLSearchParams({ tenant_id: tenantId, customer_id: customerId, page_size: "20" });
    fetch(`${ANALYTICS_BASE}/reports/journeys?${params}`)
      .then(r => r.ok ? r.json() : { data: [] })
      .then((d: { data?: Journey[] }) => { if (active) setJourneys(d.data ?? []); })
      .catch(() => { if (active) setJourneys([]); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [tenantId, customerId]);

  return { journeys, loading };
}

function useCustomerSessions(customerId: string | null) {
  const [sessions, setSessions] = useState<ContactHistoryEntry[]>([]);
  const [loading,  setLoading]  = useState(false);

  useEffect(() => {
    if (!customerId) { setSessions([]); return; }
    let active = true;
    setLoading(true);
    const url = `${ANALYTICS_BASE}/sessions/customer/${encodeURIComponent(customerId)}`
              + `?tenant_id=${encodeURIComponent(TENANT_ID)}&limit=30`;
    fetch(url)
      .then(r => r.ok ? r.json() : [])
      .then((d: ContactHistoryEntry[]) => { if (active) setSessions(Array.isArray(d) ? d : []); })
      .catch(() => { if (active) setSessions([]); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [customerId]);

  return { sessions, loading };
}

interface TranscriptMessage {
  id:        string;
  author:    string;
  text:      string;
  timestamp: string;
}

function useTranscript(sessionId: string | null) {
  const [messages, setMessages] = useState<TranscriptMessage[]>([]);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) { setMessages([]); return; }
    let active = true;
    setLoading(true);
    setError(null);
    fetch(`/api/conversation_history/${sessionId}`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then((d: TranscriptMessage[]) => { if (active) setMessages(Array.isArray(d) ? d : []); })
      .catch((e: unknown) => { if (active) { setError(String(e)); setMessages([]); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [sessionId]);

  return { messages, loading, error };
}

// ── SessionListItem ───────────────────────────────────────────────────────────

const SessionListItem: React.FC<{
  row:        SessionRow;
  selected:   boolean;
  onSelect:   () => void;
}> = ({ row, selected, onSelect }) => {
  const icon = row.isCurrent
    ? "🔵"
    : row.outcome
    ? (OUTCOME_ICON[row.outcome] ?? "📋")
    : "📋";

  return (
    <button
      onClick={onSelect}
      className={[
        "w-full text-left px-3 py-2 flex items-start gap-2 transition-colors border-b border-border last:border-0",
        selected
          ? "bg-primary-light border-l-2 border-l-primary"
          : "hover:bg-surface-muted border-l-2 border-l-transparent",
      ].join(" ")}
    >
      <span className="text-base flex-shrink-0 mt-0.5">{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs font-medium text-dark truncate">
            {poolLabel(row.poolId || "—")}
          </span>
          {row.isCurrent && (
            <span className="text-micro font-bold bg-primary-light text-primary px-1 py-0 rounded uppercase tracking-wide">
              atual
            </span>
          )}
          {row.isOrigin && !row.isCurrent && (
            <span className="text-micro font-bold bg-border text-muted px-1 py-0 rounded uppercase tracking-wide">
              origem
            </span>
          )}
        </div>
        <div className="text-2xs text-muted-light mt-0.5 flex gap-2 flex-wrap">
          <span>{formatDateTime(row.openedAt)}</span>
          {row.durationMs !== null && <span className="inline-flex items-center gap-0.5"><Timer className="w-3 h-3" aria-hidden="true" />{formatDuration(row.durationMs)}</span>}
        </div>
      </div>
    </button>
  );
};

// ── TranscriptViewer ──────────────────────────────────────────────────────────

const authorLabel = (author: string): string => {
  if (author === "customer")    return "👤 Cliente";
  if (author === "agent_human") return "👨‍💼 Agente";
  if (author === "agent_ai")    return "🤖 AI";
  if (author === "system")      return "⚙️ Sistema";
  return author;
};

const authorColor = (author: string): string => {
  if (author === "customer")    return "bg-primary-light text-primary self-start mr-8";
  if (author === "agent_human") return "bg-white border border-border text-dark self-end ml-8";
  if (author === "agent_ai")    return "bg-ai-light text-ai-text self-start mr-8";
  return "bg-surface-alt text-dark self-center text-center text-xs italic";
};

const TranscriptViewer: React.FC<{ sessionId: string | null }> = ({ sessionId }) => {
  const { messages, loading, error } = useTranscript(sessionId);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  if (!sessionId) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-muted-light">
        Selecione uma sessão para ver o transcript.
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-muted-light animate-pulse">
        Carregando transcript…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-red p-4 text-center">
        Erro ao carregar transcript: {error}
      </div>
    );
  }

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-muted-light">
        Sem mensagens registradas nesta sessão.
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-3 py-3 flex flex-col gap-2 bg-surface-muted">
      {/* Read-only banner */}
      <div className="flex items-center gap-1.5 text-2xs text-muted-light mb-1 sticky top-0 bg-surface-muted py-1">
        <span>🔒</span>
        <span>Transcript somente leitura — sessão {sessionId.slice(-8)}</span>
      </div>

      {messages.map((msg, i) => (
        <div key={msg.id ?? i} className={`flex flex-col max-w-[85%] ${authorColor(msg.author)}`}>
          <span className="text-micro font-semibold text-muted mb-0.5 px-1">
            {authorLabel(msg.author)}
          </span>
          <div className="rounded-xl px-3 py-2 text-sm leading-snug whitespace-pre-wrap break-words">
            {msg.text}
          </div>
          <span className="text-micro text-muted-light mt-0.5 px-1">
            {formatDateTime(msg.timestamp)}
          </span>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
};

// ── JourneyPanel ──────────────────────────────────────────────────────────────

export const JourneyPanel: React.FC<JourneyPanelProps> = ({
  customerId,
  tenantId,
  currentSessionId,
}) => {
  const effectiveTenantId = tenantId ?? TENANT_ID;

  const { journeys, loading: journeysLoading } = useCustomerJourneys(effectiveTenantId, customerId ?? null);
  const { sessions }                           = useCustomerSessions(customerId ?? null);

  // Pick the most relevant journey:
  // 1. One where current session is the origin
  // 2. Most recent active/suspended journey
  // 3. Most recent of any status
  const matchedJourney = React.useMemo(() => {
    if (!journeys.length) return null;
    const byOrigin = journeys.find(j => j.origin_session_id === currentSessionId);
    if (byOrigin) return byOrigin;
    const active = journeys.find(j => j.status === "active" || j.status === "suspended");
    if (active) return active;
    return journeys[0];
  }, [journeys, currentSessionId]);

  // Build session rows for the matched journey
  const sessionRows = React.useMemo((): SessionRow[] => {
    if (!matchedJourney) return [];

    const journeyStart = new Date(matchedJourney.created_at).getTime();
    const rows: SessionRow[] = [];

    // Closed sessions from customer history that are within journey timeframe
    for (const s of sessions) {
      const sessionTime = s.opened_at ? new Date(s.opened_at).getTime() : 0;
      if (sessionTime >= journeyStart - 60_000) { // 1-min grace
        rows.push({
          sessionId:  s.session_id,
          openedAt:   s.opened_at,
          poolId:     s.pool_id,
          outcome:    s.outcome,
          durationMs: s.duration_ms,
          isCurrent:  false,
          isOrigin:   s.session_id === matchedJourney.origin_session_id,
        });
      }
    }

    // Ensure current session is always present (may not be in history yet if still open)
    const alreadyHasCurrent = rows.some(r => r.sessionId === currentSessionId);
    if (!alreadyHasCurrent) {
      rows.unshift({
        sessionId:  currentSessionId,
        openedAt:   new Date().toISOString(),
        poolId:     "",
        outcome:    null,
        durationMs: null,
        isCurrent:  true,
        isOrigin:   currentSessionId === matchedJourney.origin_session_id,
      });
    } else {
      // Mark the current session in the list
      const idx = rows.findIndex(r => r.sessionId === currentSessionId);
      if (idx >= 0) rows[idx] = { ...rows[idx], isCurrent: true };
    }

    // Sort: oldest first
    rows.sort((a, b) => {
      if (!a.openedAt) return 1;
      if (!b.openedAt) return -1;
      return new Date(a.openedAt).getTime() - new Date(b.openedAt).getTime();
    });

    return rows;
  }, [matchedJourney, sessions, currentSessionId]);

  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

  // Auto-select first session when list changes
  useEffect(() => {
    if (sessionRows.length > 0 && !selectedSessionId) {
      // Default: select the current session
      const current = sessionRows.find(r => r.isCurrent);
      setSelectedSessionId(current?.sessionId ?? sessionRows[0].sessionId);
    }
  }, [sessionRows, selectedSessionId]);

  // ── No customer ──
  if (!customerId) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-muted-light text-sm gap-2 p-4">
        <span className="text-2xl">👤</span>
        <span>Cliente não identificado — sem journey disponível.</span>
      </div>
    );
  }

  // ── Loading ──
  if (journeysLoading) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-muted-light animate-pulse p-4">
        Verificando journey…
      </div>
    );
  }

  // ── Standalone (no journey) ──
  if (!matchedJourney) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-muted-light text-sm gap-2 p-4">
        <span className="text-2xl">🗂️</span>
        <span className="font-medium text-muted">Contato standalone</span>
        <span className="text-xs text-center text-muted-light max-w-xs">
          Este contato não está associado a nenhum journey. A aba Journey é usada quando
          o atendimento faz parte de um processo multi-sessão (Arc 10).
        </span>
      </div>
    );
  }

  // ── Journey view: left session list + right transcript ──
  return (
    <div className="flex flex-col flex-1 overflow-hidden">

      {/* Journey header strip */}
      <div className="flex items-center gap-2 px-3 py-2 bg-primary-light border-b border-primary/20 flex-shrink-0">
        <span className="text-sm">{STATUS_ICON[matchedJourney.status]}</span>
        <div className="flex-1 min-w-0">
          <span className="text-xs font-bold text-primary capitalize truncate block">
            {skillLabel(matchedJourney.skill_id)}
          </span>
          <span className="text-micro text-primary font-mono opacity-70">
            {matchedJourney.journey_id.slice(-8)}
          </span>
        </div>
        <span className={[
          "text-2xs font-semibold px-2 py-0.5 rounded-full",
          matchedJourney.status === "active"    ? "bg-primary-light text-primary"     :
          matchedJourney.status === "suspended" ? "bg-warning-light text-warning-text" :
          matchedJourney.status === "completed" ? "bg-green-light text-green-text"    :
          "bg-surface-alt text-muted"
        ].join(" ")}>
          {STATUS_LABEL[matchedJourney.status]}
        </span>
        <span className="text-2xs text-primary opacity-70">
          {matchedJourney.session_count} sessão{matchedJourney.session_count !== 1 ? "ões" : ""}
        </span>
      </div>

      {/* Two-pane body */}
      <div className="flex flex-1 overflow-hidden">

        {/* Left: session list */}
        <div className="w-[200px] flex-shrink-0 border-r border-border overflow-y-auto bg-white">
          {sessionRows.length === 0 ? (
            <div className="p-3 text-xs text-muted-light italic text-center">
              Carregando sessões…
            </div>
          ) : (
            sessionRows.map(row => (
              <SessionListItem
                key={row.sessionId}
                row={row}
                selected={selectedSessionId === row.sessionId}
                onSelect={() => setSelectedSessionId(row.sessionId)}
              />
            ))
          )}
        </div>

        {/* Right: read-only transcript */}
        <div className="flex flex-1 overflow-hidden flex-col">
          <TranscriptViewer sessionId={selectedSessionId} />
        </div>

      </div>
    </div>
  );
};
