/**
 * ContextoTab — Arc 11 Fase 2 (Fase D)
 *
 * Shows:
 *   0. IntentFlagsCard — intent.current + flags[] from supervisorState
 *      (migrated from EstadoTab, which was removed in Fase C)
 *   1. ContextSnapshotCard — flat ContextStore snapshot keyed by tag name
 *      (new format: context_snapshot present → preferred)
 *      + ManualTagForm — agent can add/edit tags inline → POST /api/inject-context
 *   2. ContactContextCard  — legacy structured fields (contact_context fallback)
 *   3. Historical insights — long-term memory from previous contacts
 *   4. Conversation insights — session-scoped insights
 */

import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ContactContextData,
  ContactContextField,
  ContextEntry,
  CustomerContext,
  InsightItem,
  SupervisorState,
} from "../../types";

interface ContextoTabProps {
  context:        CustomerContext | null;
  /** Session ID used for manual tag writes via POST /api/inject-context/:sessionId */
  sessionId?:     string | null;
  supervisorState?: SupervisorState | null;
  /** Logged-in user's role — used to filter ManualTagForm namespace suggestions. */
  viewerRole?:    string;
}

// ── helpers ──────────────────────────────────────────────────────────────────

function formatLastSeen(iso?: string): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
}

function confidenceColor(c: number): string {
  if (c >= 0.9) return "text-green-text bg-green-light";
  if (c >= 0.7) return "text-primary bg-primary-light";
  if (c >= 0.4) return "text-warning-text bg-warning-light";
  return "text-muted bg-border";
}

function confidenceLabel(c: number, t: (key: string) => string): string {
  if (c >= 0.9) return t('contexto.confidence_confirmed');
  if (c >= 0.7) return t('contexto.confidence_highCertainty');
  if (c >= 0.4) return t('contexto.confidence_uncertain');
  return t('contexto.confidence_unknown');
}

function sourceLabel(source: string, t: (key: string) => string): string {
  const keyMap: Record<string, string> = {
    customer_input:    'contexto.source_customerInput',
    mcp_call:          'contexto.source_mcpCall',
    ai_inferred:       'contexto.source_aiInferred',
    human_agent:       'contexto.source_humanAgent',
    insight_historico: 'contexto.source_insightHistorico',
    insight_conversa:  'contexto.source_insightConversa',
    pipeline_state:    'contexto.source_pipelineState',
    routing_engine:    'contexto.source_routingEngine',
  };
  // Handle prefixed sources like "mcp_call:mcp-server-crm:customer_get"
  // or "ai_inferred:sentiment_emitter"
  const prefix = source.split(":")[0];
  const key = keyMap[source] ?? keyMap[prefix];
  return key ? t(key) : source;
}

/** Human-readable label for a ContextStore tag name. */
function tagLabel(tag: string, t: (key: string) => string): string {
  const wellKnown: Record<string, string> = {
    "caller.nome":               t('contexto.fieldName'),
    "caller.cpf":                "CPF",
    "caller.account_id":         t('contexto.fieldAccount'),
    "caller.telefone":           t('contexto.fieldPhone'),
    "caller.email":              "E-mail",
    "caller.motivo_contato":     t('contexto.fieldReason'),
    "caller.intencao_primaria":  t('contexto.fieldIntent'),
    "caller.sentimento_atual":   t('contexto.fieldSentiment'),
    "caller.customer_id":        "Customer ID",
    "session.escalar_solicitado":  "Escalar?",
    "session.ultima_resposta":     "Última resposta IA",
    "session.historico_mensagens": "Histórico",
    "session.pergunta_coleta":     "Pergunta coleta",
    "session.sentimento.current":   "Score sentimento",
    "session.sentimento.categoria": "Categoria sentimento",
    "session.pool.id":              "Pool",
    "session.close_origin":         "Origem fechamento",
    "account.plano_atual":         "Plano",
    "account.status_conta":        "Status conta",
  };
  if (wellKnown[tag]) return wellKnown[tag];
  // Strip namespace prefix for display: "caller.nome" → "nome"
  const parts = tag.split(".");
  return parts[parts.length - 1].replace(/_/g, " ");
}

/** Group ContextStore tags by namespace (first segment before the dot). */
function groupByNamespace(
  snapshot: Record<string, ContextEntry>,
  t: (key: string) => string,
): Array<{ ns: string; label: string; entries: Array<{ tag: string; entry: ContextEntry }> }> {
  // Canonical namespace order per context-store-taxonomy.md
  const NS_ORDER = ["caller", "account", "service", "journey", "session", "agent", "history"];
  const groups: Record<string, Array<{ tag: string; entry: ContextEntry }>> = {};
  for (const [tag, entry] of Object.entries(snapshot)) {
    const ns = tag.includes(".") ? tag.split(".")[0] : "outros";
    if (!groups[ns]) groups[ns] = [];
    groups[ns].push({ tag, entry });
  }
  const orderedNs = [
    ...NS_ORDER,
    ...Object.keys(groups).filter((ns) => !NS_ORDER.includes(ns)).sort(),
  ];
  return orderedNs
    .filter((ns) => groups[ns]?.length)
    .map((ns) => ({
      ns,
      label: t(`contexto.ns_${ns}`) !== `contexto.ns_${ns}`
        ? t(`contexto.ns_${ns}`)
        : ns.charAt(0).toUpperCase() + ns.slice(1),
      entries: groups[ns],
    }));
}

// ── IntentFlagsCard ───────────────────────────────────────────────────────────
// Migrated from EstadoTab (Fase C). Shows intent.current + flags[].

const IntentFlagsCard: React.FC<{ state: SupervisorState }> = ({ state }) => {
  const { t } = useTranslation('agentAssist');
  const { intent, flags } = state;
  const hasIntent = !!intent.current;
  const hasFlags  = flags.length > 0;
  if (!hasIntent && !hasFlags) return null;

  return (
    <section className="bg-ai-light border border-ai/30 rounded-lg overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 bg-ai-light border-b border-ai/30">
        <span className="text-xs font-semibold text-ai-text uppercase tracking-wide flex-1">
          {t('contexto.intentFlags')}
        </span>
        <span className="text-2xs text-ai font-medium">
          {t('contexto.turnLabel', { count: state.turn_count })}
        </span>
      </div>

      <div className="px-3 py-2 flex flex-col gap-2">
        {/* Intent */}
        {hasIntent && (
          <div className="flex items-start gap-2">
            <span className="text-2xs text-muted w-16 shrink-0 pt-0.5">{t('contexto.intentLabel')}</span>
            <div className="flex-1 min-w-0 flex items-center gap-2 flex-wrap">
              <span className="text-xs font-medium text-dark">{intent.current}</span>
              <span className={`text-2xs px-1.5 py-0.5 rounded-full font-medium ${confidenceColor(intent.confidence)}`}>
                {(intent.confidence * 100).toFixed(0)}%
              </span>
            </div>
          </div>
        )}

        {/* Flags */}
        {hasFlags && (
          <div className="flex items-start gap-2">
            <span className="text-2xs text-muted w-16 shrink-0 pt-0.5">{t('contexto.flagsLabel')}</span>
            <div className="flex flex-wrap gap-1 flex-1">
              {flags.map((f) => (
                <span key={f}
                  className="text-2xs px-2 py-0.5 rounded-full bg-contested-light text-contested-text border border-contested/30 font-medium">
                  {f}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
};

// ── ManualTagForm ─────────────────────────────────────────────────────────────
// Allows the human agent to add/edit a ContextStore tag inline.
// Calls POST /api/inject-context/:sessionId → { key, value, confidence }.

type WriteStatus = "idle" | "busy" | "ok" | "error";

/**
 * Namespaces operators can write to (backend enforces this — datalist is UX guidance only).
 * Per context-store-taxonomy.md Phase 2: operator → agent.* and service.* only.
 */
const OPERATOR_WRITE_PREFIXES = ["agent.", "service."];
const SUPERVISOR_WRITE_PREFIXES = ["agent.", "service.", "caller.", "account.", "journey.", "history."];

const ManualTagForm: React.FC<{
  sessionId: string;
  onDone:    () => void;
  /** Viewer role — controls which namespace prefixes are suggested in the datalist. */
  viewerRole?: string;
}> = ({ sessionId, onDone, viewerRole = "operator" }) => {
  const { t } = useTranslation('agentAssist');
  const [key,    setKey]   = useState("");
  const [value,  setValue] = useState("");
  const [conf,   setConf]  = useState("0.9");
  const [status, setStatus] = useState<WriteStatus>("idle");
  const [errMsg, setErrMsg] = useState("");

  const isSupervisor = ["supervisor", "admin", "evaluator", "reviewer"].includes(viewerRole);
  const suggestedPrefixes = isSupervisor ? SUPERVISOR_WRITE_PREFIXES : OPERATOR_WRITE_PREFIXES;

  const canSubmit = key.trim().length > 0 && value.trim().length > 0 && status !== "busy";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setStatus("busy");
    setErrMsg("");
    try {
      const res = await fetch(`/api/inject-context/${sessionId}`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          key:        key.trim(),
          value:      value.trim(),
          confidence: Number(conf) || 0.9,
        }),
      });
      if (res.status === 403) {
        const body = await res.json().catch(() => ({})) as Record<string, unknown>;
        throw new Error((body["message"] as string) ?? t('contexto.noPermission'));
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setStatus("ok");
      setTimeout(() => {
        setStatus("idle");
        setKey(""); setValue(""); setConf("0.9");
        onDone();
      }, 1500);
    } catch (err) {
      setErrMsg(String(err instanceof Error ? err.message : err));
      setStatus("error");
    }
  }

  if (status === "ok") {
    return (
      <div className="flex items-center gap-1.5 text-green-text text-xs py-1.5 px-1">
        <span>✓</span>
        <span>{t('contexto.tagSaved')}</span>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-1.5 pt-2">
      {/* Tag key */}
      <div className="flex flex-col gap-0.5">
        <label className="text-2xs text-muted">{t('contexto.tagKey')}</label>
        <input
          type="text"
          value={key}
          onChange={e => setKey(e.target.value)}
          placeholder={t('contexto.tagKeyPlaceholder')}
          list="tag-prefix-suggestions"
          className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none
            focus:ring-1 focus:ring-revised/40 text-dark bg-white placeholder-muted-light"
          autoComplete="off"
        />
        <datalist id="tag-prefix-suggestions">
          {suggestedPrefixes.map(p => (
            <option key={p} value={p} />
          ))}
        </datalist>
      </div>

      {/* Value */}
      <div className="flex flex-col gap-0.5">
        <label className="text-2xs text-muted">{t('contexto.tagValue')}</label>
        <textarea
          value={value}
          onChange={e => setValue(e.target.value)}
          placeholder={t('contexto.tagValuePlaceholder')}
          rows={2}
          className="text-xs border border-border-strong rounded px-2 py-1 resize-none
            focus:outline-none focus:ring-1 focus:ring-revised/40 text-dark
            bg-white placeholder-muted-light"
          onKeyDown={e => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit(e as unknown as React.FormEvent);
          }}
        />
      </div>

      {/* Confidence */}
      <div className="flex items-center gap-2">
        <label className="text-2xs text-muted w-20 shrink-0">{t('contexto.confidence')}</label>
        <select
          value={conf}
          onChange={e => setConf(e.target.value)}
          className="text-2xs border border-border rounded px-1.5 py-0.5
            focus:outline-none focus:ring-1 focus:ring-revised/40 bg-white text-dark"
        >
          <option value="1.0">{t('contexto.conf10')}</option>
          <option value="0.9">{t('contexto.conf09')}</option>
          <option value="0.7">{t('contexto.conf07')}</option>
          <option value="0.5">{t('contexto.conf05')}</option>
        </select>
      </div>

      {/* Error */}
      {status === "error" && (
        <p className="text-2xs text-red-text">{errMsg || t('contexto.saveError')}</p>
      )}

      {/* Actions */}
      <div className="flex gap-1.5">
        <button
          type="submit"
          disabled={!canSubmit}
          className="flex-1 py-1 text-2xs font-semibold text-white
            bg-revised hover:bg-revised-text rounded transition-colors
            disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {status === "busy" ? t('contexto.saving') : t('contexto.save')}
        </button>
      </div>
    </form>
  );
};

// ── ContactContextCard ────────────────────────────────────────────────────────

interface FieldRowProps {
  label: string;
  field: ContactContextField | undefined;
  t:     (key: string, opts?: Record<string, unknown>) => string;
}

const FieldRow: React.FC<FieldRowProps> = ({ label, field, t }) => {
  if (!field) return null;
  return (
    <div className="flex items-start gap-2 py-1.5 border-b border-border last:border-0">
      <span className="text-xs text-muted w-28 shrink-0 pt-0.5">{label}</span>
      <div className="flex-1 min-w-0">
        <span className="text-sm font-medium text-dark break-all">{field.value}</span>
        <div className="flex gap-1.5 mt-0.5">
          <span className={`text-2xs px-1.5 py-0.5 rounded-full font-medium ${confidenceColor(field.confidence)}`}>
            {confidenceLabel(field.confidence, t)}
          </span>
          <span className="text-2xs text-muted-light py-0.5">
            {t('contexto.via', { source: sourceLabel(field.source, t) })}
          </span>
        </div>
      </div>
    </div>
  );
};

const ContactContextCard: React.FC<{ cc: ContactContextData }> = ({ cc }) => {
  const { t } = useTranslation('agentAssist');
  const scorePercent = cc.completeness_score !== undefined
    ? Math.round(cc.completeness_score * 100)
    : null;

  const hasData = cc.nome || cc.cpf || cc.account_id || cc.telefone || cc.email ||
                  cc.motivo_contato || cc.intencao_primaria || cc.sentimento_atual ||
                  cc.resumo_conversa;

  if (!hasData) return null;

  return (
    <section className="bg-green-light border border-green/30 rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-green-light border-b border-green/30">
        <span className="text-xs font-semibold text-green-text uppercase tracking-wide">
          {t('contexto.clientContext')}
        </span>
        {scorePercent !== null && (
          <span className={`text-2xs font-semibold px-2 py-0.5 rounded-full ${
            scorePercent >= 80
              ? "bg-green text-white"
              : scorePercent >= 50
              ? "bg-warning text-white"
              : "bg-border text-dark"
          }`}>
            {t('contexto.completeness', { pct: scorePercent })}
          </span>
        )}
      </div>

      {/* Fields */}
      <div className="px-3 py-1 divide-y divide-border">
        <FieldRow label={t('contexto.fieldName')}      field={cc.nome}              t={t} />
        <FieldRow label={t('contexto.fieldCpf')}       field={cc.cpf}               t={t} />
        <FieldRow label={t('contexto.fieldAccount')}   field={cc.account_id}        t={t} />
        <FieldRow label={t('contexto.fieldPhone')}     field={cc.telefone}          t={t} />
        <FieldRow label={t('contexto.fieldEmail')}     field={cc.email}             t={t} />
        <FieldRow label={t('contexto.fieldReason')}    field={cc.motivo_contato}    t={t} />
        <FieldRow label={t('contexto.fieldIntent')}    field={cc.intencao_primaria} t={t} />
        <FieldRow label={t('contexto.fieldSentiment')} field={cc.sentimento_atual}  t={t} />
        <FieldRow label={t('contexto.fieldSummary')}   field={cc.resumo_conversa}   t={t} />
      </div>
    </section>
  );
};

// ── ContextSnapshotCard (new ContextStore format) ─────────────────────────────

interface CtxFieldRowProps {
  tag:   string;
  entry: ContextEntry;
  t:     (key: string, opts?: Record<string, unknown>) => string;
}

const CtxFieldRow: React.FC<CtxFieldRowProps> = ({ tag, entry, t }) => {
  const isMasked = entry.masked === true;
  const isObject = entry.value !== null && entry.value !== undefined && typeof entry.value === "object";
  const displayValue =
    entry.value === null || entry.value === undefined
      ? "—"
      : isObject
      ? JSON.stringify(entry.value, null, 2)
      : String(entry.value);

  return (
    <div className="flex items-start gap-2 py-1.5 border-b border-border last:border-0">
      <span className="text-xs text-muted w-32 shrink-0 pt-0.5 capitalize">
        {tagLabel(tag, t)}
      </span>
      <div className="flex-1 min-w-0">
        {isObject ? (
          // Object values (e.g. session.pool.mentionable_pools) — pretty + scrollable
          // so they don't overflow the narrow panel.
          <pre className="text-2xs font-mono text-dark bg-surface-alt rounded px-1.5 py-1 max-h-28 overflow-auto whitespace-pre-wrap break-all">
            {displayValue}
          </pre>
        ) : (
          <span className={`text-sm font-medium break-all ${isMasked ? "text-muted-light font-mono tracking-wider" : "text-dark"}`}>
            {displayValue}
          </span>
        )}
        <div className="flex gap-1.5 mt-0.5 flex-wrap">
          {isMasked ? (
            <span className="text-2xs px-1.5 py-0.5 rounded-full font-semibold bg-warning-light text-warning-text border border-warning/30">
              🔒 PII
            </span>
          ) : (
            <span className={`text-2xs px-1.5 py-0.5 rounded-full font-medium ${confidenceColor(entry.confidence)}`}>
              {confidenceLabel(entry.confidence, t)}
            </span>
          )}
          <span className="text-2xs text-muted-light py-0.5">
            {t('contexto.via', { source: sourceLabel(entry.source, t) })}
          </span>
          {entry.visibility === "agents_only" && !isMasked && (
            <span className="text-2xs text-warning-text py-0.5">{t('contexto.agentsOnly')}</span>
          )}
        </div>
      </div>
    </div>
  );
};

const ContextSnapshotCard: React.FC<{
  snapshot:    Record<string, ContextEntry>;
  sessionId?:  string | null;
  onTagSaved?: () => void;
  viewerRole?: string;
}> = ({ snapshot, sessionId, onTagSaved, viewerRole }) => {
  const { t } = useTranslation('agentAssist');
  const [addOpen, setAddOpen] = useState(false);
  const groups = groupByNamespace(snapshot, t);
  if (groups.length === 0 && !sessionId) return null;

  return (
    <section className="bg-revised-light border border-revised/30 rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-revised-light border-b border-revised/30">
        <span className="text-xs font-semibold text-revised-text uppercase tracking-wide">
          {t('contexto.contextStore')}
        </span>
        <div className="flex items-center gap-2">
          <span className="text-2xs text-revised font-medium">
            {t('contexto.fields', { count: Object.keys(snapshot).length })}
          </span>
          {sessionId && (
            <button
              onClick={() => setAddOpen(v => !v)}
              title={addOpen ? t('contexto.cancel') : t('contexto.addTag')}
              className={[
                "w-5 h-5 flex items-center justify-center rounded text-xs font-bold transition-colors",
                addOpen
                  ? "bg-revised text-white"
                  : "text-revised hover:bg-revised-light",
              ].join(" ")}
            >
              {addOpen ? "×" : "+"}
            </button>
          )}
        </div>
      </div>

      {/* Manual tag form (toggleable) */}
      {addOpen && sessionId && (
        <div className="px-3 pb-2 border-b border-revised/20 bg-revised-light/60">
          <ManualTagForm
            sessionId={sessionId}
            viewerRole={viewerRole}
            onDone={() => {
              setAddOpen(false);
              onTagSaved?.();
            }}
          />
        </div>
      )}

      {/* Groups */}
      {groups.map(({ ns, label, entries }) => (
        <div key={ns}>
          <div className="px-3 pt-2 pb-0.5">
            <span className="text-2xs font-semibold text-revised-text uppercase tracking-wider">
              {label}
            </span>
          </div>
          <div className="px-3 pb-1">
            {entries.map(({ tag, entry }) => (
              <CtxFieldRow key={tag} tag={tag} entry={entry} t={t} />
            ))}
          </div>
        </div>
      ))}

      {/* Empty state when no tags yet but form available */}
      {groups.length === 0 && (
        <p className="px-3 py-2 text-xs text-revised italic">
          {t('contexto.empty')}
        </p>
      )}
    </section>
  );
};

// ── InsightCard ───────────────────────────────────────────────────────────────

const InsightCard: React.FC<{ item: InsightItem; historical: boolean }> = ({
  item,
  historical,
}) => {
  const { t } = useTranslation('agentAssist');
  return (
    <div
      className={`rounded-lg p-2.5 text-sm leading-snug ${
        historical
          ? "bg-primary-light border border-primary/30 text-primary"
          : "bg-ai-light border border-ai/30 text-ai-text"
      }`}
    >
      <p>{item.content}</p>
      <div className="flex gap-3 mt-1 text-2xs text-muted">
        {item.confidence !== undefined && (
          <span>{t('contexto.insightConfidence', { pct: (item.confidence * 100).toFixed(0) })}</span>
        )}
        {item.last_seen && (
          <span>{t('contexto.insightSeen', { date: formatLastSeen(item.last_seen) })}</span>
        )}
        {item.turn !== undefined && (
          <span>{t('contexto.insightTurn', { count: item.turn })}</span>
        )}
      </div>
    </div>
  );
};

// ── ContextoTab ───────────────────────────────────────────────────────────────

export const ContextoTab: React.FC<ContextoTabProps> = ({
  context,
  sessionId,
  supervisorState,
  viewerRole = "operator",
}) => {
  const { t } = useTranslation('agentAssist');

  // Callback passed to ContextSnapshotCard so a supervisor_state refresh
  // can be triggered after a successful tag write (parent must poll or trigger).
  // For now we just log — the 3s polling in useSupervisorState will pick it up.
  const handleTagSaved = () => {
    /* no-op: useSupervisorState polls every 3s; tag will appear on next fetch */
  };

  if (!context) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-muted-light p-4 h-full">
        {t('contexto.waiting')}
      </div>
    );
  }

  const {
    historical_insights,
    conversation_insights,
    contact_context,
    context_snapshot,
  } = context;

  return (
    <div className="flex flex-col gap-4 p-3 overflow-y-auto h-full">

      {/* ── 0. Intent + Flags (from supervisorState, migrated from EstadoTab) ── */}
      {supervisorState && (
        <IntentFlagsCard state={supervisorState} />
      )}

      {/* ── 1. ContextStore snapshot (new, preferred) ── */}
      {context_snapshot && (
        <ContextSnapshotCard
          snapshot={context_snapshot}
          sessionId={sessionId}
          viewerRole={viewerRole}
          onTagSaved={handleTagSaved}
        />
      )}

      {/* ── When no context_snapshot yet, still show the write form ── */}
      {!context_snapshot && sessionId && (
        <section className="bg-revised-light border border-revised/30 rounded-lg overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 bg-revised-light border-b border-revised/30">
            <span className="text-xs font-semibold text-revised-text uppercase tracking-wide">
              {t('contexto.contextStore')}
            </span>
            <span className="text-2xs text-revised italic">{t('contexto.storeEmpty')}</span>
          </div>
          <div className="px-3 pb-3">
            <ManualTagForm sessionId={sessionId} viewerRole={viewerRole} onDone={handleTagSaved} />
          </div>
        </section>
      )}

      {/* ── 2. Legacy Contact Context (fallback when no context_snapshot) ── */}
      {!context_snapshot && contact_context && (
        <ContactContextCard cc={contact_context} />
      )}

      {/* ── 3. Historical Insights ── */}
      <section>
        <h3 className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">
          {t('contexto.historicalInsights', { count: historical_insights.length })}
        </h3>
        {historical_insights.length === 0 ? (
          <p className="text-xs text-muted-light">
            {t('contexto.noHistory')}
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {historical_insights.map((item, i) => (
              <InsightCard key={i} item={item} historical={true} />
            ))}
          </div>
        )}
      </section>

      {/* ── 4. Conversation Insights ── */}
      <section>
        <h3 className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">
          {t('contexto.conversationInsights', { count: conversation_insights.length })}
        </h3>
        {conversation_insights.length === 0 ? (
          <p className="text-xs text-muted-light">
            {t('contexto.noInsights')}
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {conversation_insights.map((item, i) => (
              <InsightCard key={i} item={item} historical={false} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
};
