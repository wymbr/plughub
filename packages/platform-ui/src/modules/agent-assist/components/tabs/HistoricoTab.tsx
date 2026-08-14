/**
 * HistoricoTab
 * Shows the customer's last N closed sessions from analytics-api.
 *
 * Each session row shows: date, channel icon, duration, outcome badge, close_reason.
 * Clicking a row expands it to show pool_id/session_id and lazily loads the MASKED
 * transcript of that past contact inline (Customer History H1 — drill-down).
 *
 * Note (Arc 19 Fase F): "Processos em aberto" (Open Journeys) section removed —
 * Journey entity eliminated. Session history remains unchanged.
 */

import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Timer, User, Search, SlidersHorizontal, X, GitBranch, ShieldCheck } from "lucide-react";
import { useAuth } from "../../../../auth/useAuth";
import { ContactHistoryEntry, SearchHit, TranscriptMessage } from "../../types";
import { useCustomerHistory } from "../../hooks/useCustomerHistory";
import { useCustomerSearch, SearchFilters } from "../../hooks/useCustomerSearch";
import { useCustomerJourneys } from "../../hooks/useCustomerJourneys";
import { useSessionTrace, TraceNode } from "../../hooks/useSessionTrace";
import { useSessionTranscript } from "../../hooks/useSessionTranscript";

interface HistoricoTabProps {
  customerId:  string | null;
  tenantId?:   string | null;   // retained for API compatibility — unused after Arc 19 Fase F
  /** Sessão atual — quando presente e membro de uma journey, mostra a seção Processo
   *  (útil p/ contatos de workflow/aprovação que não têm customer_id). */
  sessionId?:  string | null;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function channelIcon(channel: string): string {
  switch (channel) {
    case "webchat":  return "💬";
    case "whatsapp": return "📱";
    case "voice":    return "📞";
    case "email":    return "✉️";
    case "sms":      return "💬";
    default:         return "🔗";
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      day:    "2-digit",
      month:  "2-digit",
      year:   "numeric",
      hour:   "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function formatDuration(ms: number | null): string {
  if (ms === null || ms === undefined) return "—";
  const totalSeconds = Math.round(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return `${minutes}m${seconds > 0 ? ` ${seconds}s` : ""}`;
  const hours = Math.floor(minutes / 60);
  const mins  = minutes % 60;
  return `${hours}h${mins > 0 ? ` ${mins}m` : ""}`;
}

function formatTime(iso: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour:   "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

function OutcomeBadge({ outcome }: { outcome: string | null }): JSX.Element {
  const { t } = useTranslation('agentAssist');
  const colorMap: Record<string, string> = {
    resolved:  "bg-green-light text-green-text",
    escalated: "bg-warning-light text-warning-text",
    abandoned: "bg-red-light text-red-text",
  };
  const labelKey = outcome && ['resolved','escalated','abandoned'].includes(outcome)
    ? `historico.outcome.${outcome}`
    : null;
  const label = labelKey ? t(labelKey) : (outcome ?? "—");
  const color = colorMap[outcome ?? ""] ?? "bg-surface-alt text-dark";
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-2xs font-medium ${color}`}>
      {label}
    </span>
  );
}

// ── Transcript drill-down (H1) ────────────────────────────────────────────────

const TranscriptBubble: React.FC<{ msg: TranscriptMessage }> = ({ msg }) => {
  const isCustomer = msg.author_role === "customer";
  const isInternal = msg.visibility === "agents_only";
  const role       = msg.author_role || "—";
  return (
    <div className={`flex flex-col ${isCustomer ? "items-start" : "items-end"}`}>
      <div
        className={`max-w-[85%] rounded-lg px-2.5 py-1.5 text-2xs leading-snug ${
          isInternal
            ? "bg-warning-light text-warning-text border border-dashed border-warning/40"
            : isCustomer
              ? "bg-surface-alt text-dark"
              : "bg-primary/10 text-dark"
        }`}
      >
        <div className="flex items-center gap-1.5 mb-0.5">
          <span className="text-2xs font-semibold uppercase tracking-wide text-muted-light">
            {role}
          </span>
          <span className="text-2xs text-muted-light">{formatTime(msg.created_at)}</span>
        </div>
        <div className="whitespace-pre-wrap break-words">{msg.content}</div>
      </div>
    </div>
  );
};

const TranscriptView: React.FC<{ sessionId: string }> = ({ sessionId }) => {
  const { t } = useTranslation('agentAssist');
  const { messages, loading, error } = useSessionTranscript(sessionId);

  return (
    <div className="mt-2 pt-2 border-t border-border">
      <div className="text-2xs font-semibold uppercase tracking-wide text-muted-light mb-1.5">
        {t('historico.transcriptTitle')}
      </div>

      {loading && (
        <div className="text-2xs text-muted-light animate-pulse py-2">
          {t('historico.loadingTranscript')}
        </div>
      )}

      {error && (
        <div className="text-2xs text-red-text bg-red-light border border-red/30 rounded p-1.5">
          {t('historico.transcriptError', { error })}
        </div>
      )}

      {!loading && !error && messages.length === 0 && (
        <div className="text-2xs text-muted-light py-2">
          {t('historico.noMessages')}
        </div>
      )}

      {messages.length > 0 && (
        <>
          <div className="flex flex-col gap-1.5 max-h-64 overflow-y-auto pr-1">
            {messages.map((m) => (
              <TranscriptBubble key={m.stream_entry_id} msg={m} />
            ))}
          </div>
          <div className="text-2xs text-muted-light mt-1.5 italic">
            {t('historico.maskedNote')}
          </div>
        </>
      )}
    </div>
  );
};

// ── Entry row ─────────────────────────────────────────────────────────────────

const HistoryRow: React.FC<{ entry: ContactHistoryEntry }> = ({ entry }) => {
  const { t } = useTranslation('agentAssist');
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState(false);
  // Bidirectional nav: this contact belongs to a multi-session process → link to it.
  const partOfProcess = !!entry.root_session_id && entry.root_session_id !== entry.session_id;

  return (
    <div className="border border-border rounded-lg overflow-hidden">
      {/* Summary row */}
      <button
        className="w-full text-left px-3 py-2 flex items-center gap-2 hover:bg-surface-muted transition-colors"
        onClick={() => setExpanded((e) => !e)}
      >
        <span className="text-base shrink-0" aria-hidden>
          {channelIcon(entry.channel)}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <OutcomeBadge outcome={entry.outcome} />
            <span className="text-xs text-muted truncate">
              {formatDate(entry.opened_at)}
            </span>
          </div>
          <div className="text-xs text-muted-light mt-0.5 flex gap-2 items-center">
            <span className="inline-flex items-center gap-0.5"><Timer className="w-3 h-3" aria-hidden="true" />{formatDuration(entry.duration_ms)}</span>
            {entry.close_reason && (
              <span className="truncate">{entry.close_reason}</span>
            )}
            {partOfProcess && (
              <span
                onClick={(e) => { e.stopPropagation(); navigate(`/analise/sessions?journey=${encodeURIComponent(entry.root_session_id!)}`); }}
                title={t('historico.journeys.openHint')}
                className="inline-flex items-center gap-0.5 text-primary hover:underline cursor-pointer font-mono shrink-0"
              >
                <GitBranch className="w-3 h-3" aria-hidden="true" />{journeyLabel(entry.root_session_id!)}
              </span>
            )}
          </div>
        </div>
        <span className="text-muted-light text-xs shrink-0">
          {expanded ? "▲" : "▼"}
        </span>
      </button>

      {/* Expanded details */}
      {expanded && (
        <div className="px-3 pb-2 pt-0 bg-surface-muted border-t border-border text-xs text-muted space-y-1">
          <div>
            <span className="font-medium text-muted">{t('historico.pool')}:</span>{" "}
            {entry.pool_id || "—"}
          </div>
          <div>
            <span className="font-medium text-muted">{t('historico.channel')}:</span>{" "}
            {entry.channel}
          </div>
          {entry.closed_at && (
            <div>
              <span className="font-medium text-muted">{t('historico.closedAt')}:</span>{" "}
              {formatDate(entry.closed_at)}
            </div>
          )}
          <div className="font-mono text-2xs text-muted-light truncate">
            {entry.session_id}
          </div>

          {/* Drill-down: masked transcript loaded lazily on expand (H1) */}
          <TranscriptView sessionId={entry.session_id} />
        </div>
      )}
    </div>
  );
};

// ── Search hit row (H3) ───────────────────────────────────────────────────────
// Like HistoryRow, but shows the masked snippet + match count; expand → drill (H1).

const SearchHitRow: React.FC<{ hit: SearchHit }> = ({ hit }) => {
  const { t } = useTranslation('agentAssist');
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <button
        className="w-full text-left px-3 py-2 flex items-start gap-2 hover:bg-surface-muted transition-colors"
        onClick={() => setExpanded((e) => !e)}
      >
        <span className="text-base shrink-0 pt-0.5" aria-hidden>
          {channelIcon(hit.channel)}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <OutcomeBadge outcome={hit.outcome} />
            <span className="text-xs text-muted truncate">{formatDate(hit.opened_at)}</span>
            {hit.score > 1 && (
              <span className="text-2xs px-1.5 py-0.5 rounded-full bg-primary-light text-primary font-medium">
                {t('historico.search.matches', { count: hit.score })}
              </span>
            )}
          </div>
          {/* Masked snippet with the match context */}
          <p className="text-xs text-dark mt-1 leading-snug break-words line-clamp-2">
            {hit.snippet || <span className="text-muted-light italic">{t('historico.search.noSnippet')}</span>}
          </p>
        </div>
        <span className="text-muted-light text-xs shrink-0 pt-0.5">{expanded ? "▲" : "▼"}</span>
      </button>

      {expanded && (
        <div className="px-3 pb-2 pt-0 bg-surface-muted border-t border-border text-xs text-muted space-y-1">
          <div className="font-mono text-2xs text-muted-light truncate pt-1">{hit.session_id}</div>
          {/* Drill-down: masked transcript loaded lazily on expand (H1) */}
          <TranscriptView sessionId={hit.session_id} />
        </div>
      )}
    </div>
  );
};

// ── Cliente 360 / HJ: jornadas (processos) em aberto do cliente ───────────────
// Distingue um PROCESSO (PRC-…, uma árvore de sessões) de um CONTATO individual
// (uma sessão). Os chips linkam pra Vista Processos / rastro T6 (Analytics).
// Só PROCESSOS reais (significant: multi-sessão / webhook) aparecem aqui — contatos
// avulsos ficam na lista "Contatos anteriores" abaixo.
function journeyLabel(id: string): string {
  return `PRC-${id.slice(0, 8)}`;
}

const JourneysSection: React.FC<{ customerId: string | null }> = ({ customerId }) => {
  const { t } = useTranslation('agentAssist');
  const navigate = useNavigate();
  const { journeys } = useCustomerJourneys(customerId);

  const open = journeys.filter(j => j.open_count > 0);
  if (open.length === 0) return null;

  return (
    <div className="mb-3">
      <div className="text-xs font-semibold text-muted uppercase tracking-wide mb-1.5 flex items-center gap-1.5">
        <GitBranch className="w-3.5 h-3.5" aria-hidden="true" />
        {t('historico.journeys.title')}
      </div>
      <div className="flex flex-col gap-1.5">
        {open.map(j => (
          <button
            key={j.journey_id}
            onClick={() => navigate(`/analise/sessions?journey=${encodeURIComponent(j.journey_id)}`)}
            title={t('historico.journeys.openHint')}
            className="text-left border border-primary/20 bg-primary-light/30 rounded-lg px-3 py-2 hover:bg-primary-light/50 transition-colors"
          >
            <div className="flex items-center gap-1.5">
              <span className="font-mono text-xs text-primary font-medium">{journeyLabel(j.journey_id)}</span>
              <span className="text-2xs px-1.5 py-0.5 rounded-full bg-warning-light text-warning-text font-medium">
                {t('historico.journeys.open')}
              </span>
              <span className="flex-1" />
              <span className="text-muted-light text-xs" aria-hidden="true">↗</span>
            </div>
            <div className="text-2xs text-muted-light mt-0.5">
              {t('historico.journeys.sessions', { count: j.session_count })}
              {j.channels?.length ? ` · ${j.channels.join(', ')}` : ''}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
};

// ── ProcessSection (follow-up a) ──────────────────────────────────────────────
// Para um contato que é membro de uma JOURNEY (ex.: tarefa de aprovação de workflow,
// que raramente tem customer_id), mostra as sessões do PROCESSO via o rastro T6
// (proveniência em volta desta sessão). Link pra Vista Processos. Só aparece quando
// há mais de uma sessão (processo real, não sessão avulsa).
const ProcessSection: React.FC<{ sessionId: string | null }> = ({ sessionId }) => {
  const { t } = useTranslation('agentAssist');
  const navigate = useNavigate();
  const { trace } = useSessionTrace(sessionId);

  const nodes = trace?.nodes ?? [];
  if (nodes.length <= 1) return null;   // sessão avulsa → não é "processo"

  const journeyId = trace?.focus_journey_id ?? trace?.focus?.root_session_id ?? null;
  const label = (n: TraceNode) => n.pool_id || n.channel || n.session_id.slice(0, 8);

  return (
    <div className="mb-3">
      <div className="text-xs font-semibold text-muted uppercase tracking-wide mb-1.5 flex items-center gap-1.5">
        <GitBranch className="w-3.5 h-3.5" aria-hidden="true" />
        {t('historico.process.title', { defaultValue: 'Process' })}
        {journeyId && (
          <button
            onClick={() => navigate(`/analise/sessions?journey=${encodeURIComponent(journeyId)}`)}
            title={t('historico.process.openHint', { defaultValue: 'Open in Process view' })}
            className="ml-auto font-mono text-2xs text-primary hover:text-primary-dark"
          >
            {journeyLabel(journeyId)} ↗
          </button>
        )}
      </div>
      <div className="flex flex-col gap-1">
        {nodes.map(n => {
          const isFocus = n.depth === 0;
          return (
            <div
              key={n.session_id}
              className={`rounded-lg px-3 py-1.5 border text-xs ${
                isFocus ? "border-primary/40 bg-primary-light/30" : "border-border"
              }`}
            >
              <div className="flex items-center gap-1.5">
                <span className="font-medium text-dark truncate">{label(n)}</span>
                {isFocus && <span className="text-2xs px-1.5 py-0.5 rounded-full bg-primary text-white">{t('historico.process.thisSession', { defaultValue: 'this task' })}</span>}
                <span className="flex-1" />
                {n.outcome && <span className="text-2xs text-muted-light">{n.outcome}</span>}
              </div>
              <div className="text-2xs text-muted-light mt-0.5 flex items-center gap-1.5">
                <span className="font-mono">{n.session_id.slice(0, 8)}</span>
                {n.channel && <span>· {n.channel}</span>}
                {n.spawn_reason && <span>· {n.spawn_reason}</span>}
                {n.journey_boundary && <span className="text-warning-text">· {t('historico.process.boundary', { defaultValue: 'other process' })}</span>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

// ── ApprovalAuditSection (A5.6) ───────────────────────────────────────────────
// Trilha append-only das decisões de aprovação desta sessão, lida da STREAM canônica
// via mcp-server (GET /api/approval_audit/:sessionId — ClickHouse não é alimentado pela
// stream). Gated no backend por ABAC approvals.operacao; some quando vazio/sem acesso.

interface ApprovalFieldEditView { field: string; before: string | null; after: string | null }
interface ApprovalDecisionMetaView {
  choice: string;
  edits?: ApprovalFieldEditView[];
  principal_type: string;
  decided_by: string;
  verification_class: string;
  threshold_in_force?: string;
  attachments_viewed?: string[];
  decided_at: string;
}
interface ApprovalDecisionView {
  entry_id: string;
  author_id: string | null;
  author_role: string | null;
  timestamp: string | null;
  approval: ApprovalDecisionMetaView;
}

const ApprovalAuditSection: React.FC<{ sessionId: string | null }> = ({ sessionId }) => {
  const { t } = useTranslation('agentAssist');
  const { getAccessToken } = useAuth();
  const [decisions, setDecisions] = useState<ApprovalDecisionView[]>([]);

  useEffect(() => {
    if (!sessionId) { setDecisions([]); return; }
    let cancelled = false;
    (async () => {
      try {
        const token = await getAccessToken();
        const res = await fetch(`/api/approval_audit/${encodeURIComponent(sessionId)}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) { if (!cancelled) setDecisions([]); return; }
        const data = await res.json();
        if (!cancelled) setDecisions(Array.isArray(data.decisions) ? data.decisions : []);
      } catch { if (!cancelled) setDecisions([]); }
    })();
    return () => { cancelled = true; };
  }, [sessionId, getAccessToken]);

  if (!sessionId || decisions.length === 0) return null;

  return (
    <div className="mb-3">
      <div className="text-xs font-semibold text-muted uppercase tracking-wide mb-1.5 flex items-center gap-1.5">
        <ShieldCheck className="w-3.5 h-3.5" aria-hidden="true" />
        {t('historico.approval.title', { defaultValue: 'Approval decisions' })}
      </div>
      <div className="flex flex-col gap-1.5">
        {decisions.map((d) => {
          const a = d.approval;
          const changed = (a.edits ?? []).filter((e) => e.before !== e.after);
          const belowThreshold = !!a.threshold_in_force && a.verification_class !== a.threshold_in_force;
          return (
            <div key={d.entry_id} className="border border-border rounded-lg px-3 py-2 text-xs">
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="px-1.5 py-0.5 rounded text-2xs font-medium bg-primary/10 text-primary">{a.choice}</span>
                <span className={`px-1.5 py-0.5 rounded-full text-2xs font-medium ${
                  a.verification_class === 'possessed' ? 'bg-green-light text-green-text' : 'bg-warning-light text-warning-text'
                }`}>
                  {t(`historico.approval.class.${a.verification_class}`, { defaultValue: a.verification_class })}
                </span>
                {belowThreshold && (
                  <span className="text-2xs text-warning-text">
                    {t('historico.approval.required', { defaultValue: 'required' })}: {a.threshold_in_force}
                  </span>
                )}
              </div>
              <div className="text-2xs text-muted-light mt-1 flex items-center gap-1.5 flex-wrap">
                <span>{t(`historico.approval.principal.${a.principal_type}`, { defaultValue: a.principal_type })}</span>
                <span className="font-mono truncate">{a.decided_by || '—'}</span>
                <span>· {formatDate(a.decided_at)}</span>
              </div>
              {changed.length > 0 && (
                <div className="mt-1.5 pt-1.5 border-t border-border space-y-0.5">
                  <div className="text-2xs font-semibold text-muted-light uppercase tracking-wide">
                    {t('historico.approval.edits', { defaultValue: 'Edits' })}
                  </div>
                  {changed.map((e, i) => (
                    <div key={i} className="text-2xs text-muted flex items-center gap-1 flex-wrap">
                      <span className="font-medium">{e.field}:</span>
                      <span className="line-through text-muted-light">{e.before ?? '∅'}</span>
                      <span aria-hidden>→</span>
                      <span className="text-dark">{e.after ?? '∅'}</span>
                    </div>
                  ))}
                </div>
              )}
              {(a.attachments_viewed?.length ?? 0) > 0 && (
                <div className="text-2xs text-muted-light mt-1">
                  {t('historico.approval.attachmentsViewed', { defaultValue: 'Attachments viewed' })}: {a.attachments_viewed!.length}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

// ── HistoricoTab ──────────────────────────────────────────────────────────────

export const HistoricoTab: React.FC<HistoricoTabProps> = ({ customerId, sessionId }) => {
  const { t } = useTranslation('agentAssist');
  const { entries, loading, error, refetch } = useCustomerHistory(customerId);

  // ── H3: search state ──
  const [query,       setQuery]       = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const [filters,     setFilters]     = useState<SearchFilters>({});
  const { hits, loading: searching, error: searchError, active } =
    useCustomerSearch(customerId, query, filters);

  const setFilter = (k: keyof SearchFilters, v: string) =>
    setFilters((f) => ({ ...f, [k]: v || undefined }));
  const hasFilters = !!(filters.from || filters.to || filters.channel || filters.outcome);
  const clearSearch = () => { setQuery(""); setFilters({}); setShowFilters(false); };

  if (!customerId) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-sm text-muted-light p-4 gap-2">
        <User className="w-8 h-8 text-muted-light" aria-hidden="true" />
        <span>{t('historico.noCustomer')}</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border flex-shrink-0">
        <span className="text-xs font-semibold text-muted uppercase tracking-wide">
          {t('historico.previousContacts')}
        </span>
        <button
          onClick={refetch}
          disabled={loading}
          className="text-xs text-primary hover:text-primary-dark disabled:opacity-50 transition-colors"
          title={t('historico.reload')}
        >
          {loading ? "…" : "↻"}
        </button>
      </div>

      {/* ── H3: search bar + filters ── */}
      <div className="px-3 py-2 border-b border-border flex-shrink-0 flex flex-col gap-1.5">
        <div className="flex items-center gap-1.5">
          <div className="relative flex-1">
            <Search className="w-3.5 h-3.5 text-muted-light absolute left-2 top-1/2 -translate-y-1/2" aria-hidden="true" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('historico.search.placeholder')}
              className="w-full text-xs border border-border-strong rounded pl-7 pr-6 py-1.5
                focus:outline-none focus:ring-1 focus:ring-primary/40 text-dark bg-white placeholder-muted-light"
            />
            {query && (
              <button
                onClick={clearSearch}
                title={t('historico.search.clear')}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-light hover:text-dark"
              >
                <X className="w-3.5 h-3.5" aria-hidden="true" />
              </button>
            )}
          </div>
          <button
            onClick={() => setShowFilters((v) => !v)}
            title={t('historico.search.filters')}
            className={`p-1.5 rounded border transition-colors ${
              showFilters || hasFilters
                ? "border-primary/40 text-primary bg-primary-light"
                : "border-border-strong text-muted-light hover:text-dark"
            }`}
          >
            <SlidersHorizontal className="w-3.5 h-3.5" aria-hidden="true" />
          </button>
        </div>

        {showFilters && (
          <div className="grid grid-cols-2 gap-1.5 pt-1">
            <input type="date" value={filters.from ?? ""} onChange={(e) => setFilter("from", e.target.value)}
              title={t('historico.search.from')}
              className="text-2xs border border-border rounded px-1.5 py-1 text-dark bg-white
                focus:outline-none focus:ring-1 focus:ring-primary/40" />
            <input type="date" value={filters.to ?? ""} onChange={(e) => setFilter("to", e.target.value)}
              title={t('historico.search.to')}
              className="text-2xs border border-border rounded px-1.5 py-1 text-dark bg-white
                focus:outline-none focus:ring-1 focus:ring-primary/40" />
            <select value={filters.channel ?? ""} onChange={(e) => setFilter("channel", e.target.value)}
              className="text-2xs border border-border rounded px-1.5 py-1 bg-white text-dark
                focus:outline-none focus:ring-1 focus:ring-primary/40">
              <option value="">{t('historico.search.allChannels')}</option>
              {["webchat", "whatsapp", "voice", "email", "sms"].map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <select value={filters.outcome ?? ""} onChange={(e) => setFilter("outcome", e.target.value)}
              className="text-2xs border border-border rounded px-1.5 py-1 bg-white text-dark
                focus:outline-none focus:ring-1 focus:ring-primary/40">
              <option value="">{t('historico.search.allOutcomes')}</option>
              {["resolved", "escalated", "abandoned"].map((o) => (
                <option key={o} value={o}>{t(`historico.outcome.${o}`, { defaultValue: o })}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* ── Content: search results when a term is set, else the history list ── */}
      <div className="flex-1 overflow-y-auto p-3">
        {active ? (
          <>
            {searching && hits.length === 0 && (
              <div className="flex items-center justify-center py-8">
                <span className="text-sm text-muted-light animate-pulse">{t('historico.search.searching')}</span>
              </div>
            )}
            {searchError && (
              <div className="text-xs text-red-text bg-red-light border border-red/30 rounded p-2">
                {t('historico.search.error', { error: searchError })}
              </div>
            )}
            {!searching && !searchError && hits.length === 0 && (
              <div className="flex flex-col items-center justify-center py-8 text-sm text-muted-light gap-1">
                <Search className="w-6 h-6 opacity-40" aria-hidden="true" />
                <span>{t('historico.search.noResults')}</span>
              </div>
            )}
            {hits.length > 0 && (
              <div className="flex flex-col gap-2">
                <div className="text-2xs text-muted-light px-0.5">
                  {t('historico.search.resultCount', { count: hits.length })}
                </div>
                {hits.map((hit) => (
                  <SearchHitRow key={hit.session_id} hit={hit} />
                ))}
              </div>
            )}
          </>
        ) : (
          <>
            {/* A5.6 — trilha de decisões de aprovação desta sessão (stream canônica) */}
            <ApprovalAuditSection sessionId={sessionId ?? null} />
            {/* Follow-up (a) — o PROCESSO desta sessão (workflow/aprovação sem customer_id) */}
            <ProcessSection sessionId={sessionId ?? null} />
            {/* HJ — jornadas (processos) em aberto do cliente, distintas dos contatos */}
            <JourneysSection customerId={customerId} />

            {loading && entries.length === 0 && (
              <div className="flex items-center justify-center py-8">
                <span className="text-sm text-muted-light animate-pulse">
                  {t('historico.loadingHistory')}
                </span>
              </div>
            )}

            {error && (
              <div className="text-xs text-red-text bg-red-light border border-red/30 rounded p-2">
                {t('historico.historyError', { error })}
              </div>
            )}

            {!loading && !error && entries.length === 0 && (
              <div className="flex flex-col items-center justify-center h-full text-sm text-muted-light gap-1">
                <span className="text-xl">🗂</span>
                <span>{t('historico.noHistory')}</span>
              </div>
            )}

            {entries.length > 0 && (
              <div className="flex flex-col gap-2">
                {entries.map((entry) => (
                  <HistoryRow key={entry.session_id} entry={entry} />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};
