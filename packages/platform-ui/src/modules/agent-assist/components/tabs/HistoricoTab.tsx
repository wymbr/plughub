/**
 * HistoricoTab
 * Shows:
 *  1. "Processos em aberto" — active/suspended Journeys for this customer (Arc 10 Phase D)
 *  2. "Contatos anteriores" — the customer's last N closed sessions from analytics-api.
 *
 * Each session row shows: date, channel icon, duration, outcome badge, close_reason.
 * Clicking a row expands it to show pool_id and session_id for reference.
 */

import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Timer, User } from "lucide-react";
import { ContactHistoryEntry } from "../../types";
import { useCustomerHistory } from "../../hooks/useCustomerHistory";
import type { Journey, JourneyStatus } from "@/modules/workflows/api/hooks";

interface HistoricoTabProps {
  customerId:  string | null;
  tenantId?:   string | null;
}

// ── Open journeys for this customer (Arc 10 Phase D) ─────────────────────────

const JOURNEY_STATUS_COLORS: Record<JourneyStatus, string> = {
  active:    '#2563eb',
  suspended: '#d97706',
  completed: '#059669',
  failed:    '#dc2626',
  cancelled: '#6b7280',
}

function useCustomerJourneys(tenantId: string | null | undefined, customerId: string | null) {
  const [journeys, setJourneys] = useState<Journey[]>([])
  const [loading,  setLoading]  = useState(false)

  useEffect(() => {
    if (!tenantId || !customerId) { setJourneys([]); return }
    let active = true
    setLoading(true)
    const params = new URLSearchParams({
      tenant_id:   tenantId,
      customer_id: customerId,
      page_size:   '10',
    })
    fetch(`/analytics/reports/journeys?${params.toString()}`)
      .then(r => r.ok ? r.json() : { data: [] })
      .then((d: { data?: Journey[] }) => {
        if (active) {
          // Show active and suspended only — completed/failed/cancelled are noise
          setJourneys((d.data ?? []).filter(j => j.status === 'active' || j.status === 'suspended'))
        }
      })
      .catch(() => { if (active) setJourneys([]) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [tenantId, customerId])

  return { journeys, loading }
}

function OpenJourneys({ tenantId, customerId }: { tenantId: string | null | undefined; customerId: string | null }) {
  const { t } = useTranslation('agentAssist');
  const { journeys, loading } = useCustomerJourneys(tenantId, customerId)

  if (!loading && journeys.length === 0) return null

  return (
    <div className="px-3 pt-2 pb-0 flex-shrink-0">
      <div className="text-2xs font-semibold text-muted-light uppercase tracking-wide mb-1.5">
        {t('historico.openJourneys')}
      </div>
      {loading && (
        <div className="text-xs text-muted-light animate-pulse py-1">{t('historico.loading')}</div>
      )}
      {!loading && journeys.map(j => {
        const color = JOURNEY_STATUS_COLORS[j.status]
        return (
          <div key={j.journey_id}
            className="flex items-start justify-between gap-2 px-2.5 py-1.5 rounded-lg border mb-1.5"
            style={{ borderColor: color + '40', background: color + '10' }}>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-mono text-muted truncate font-medium">
                {j.skill_id.replace(/^skill_|_v\d+$/g, '').replace(/_/g, ' ')}
              </div>
              <div className="text-2xs text-muted-light mt-0.5">
                {t(j.session_count === 1 ? 'historico.sessions_one' : 'historico.sessions_other', { count: j.session_count })}
              </div>
            </div>
            <span className="text-2xs font-semibold px-1.5 py-0.5 rounded flex-shrink-0"
              style={{ background: color + '25', color }}>
              {t(`historico.journeyStatus.${j.status}`)}
            </span>
          </div>
        )
      })}
      <div className="border-b border-border mt-1 mb-2" />
    </div>
  )
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

// ── Entry row ─────────────────────────────────────────────────────────────────

const HistoryRow: React.FC<{ entry: ContactHistoryEntry }> = ({ entry }) => {
  const { t } = useTranslation('agentAssist');
  const [expanded, setExpanded] = useState(false);

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
          <div className="text-xs text-muted-light mt-0.5 flex gap-2">
            <span className="inline-flex items-center gap-0.5"><Timer className="w-3 h-3" aria-hidden="true" />{formatDuration(entry.duration_ms)}</span>
            {entry.close_reason && (
              <span className="truncate">{entry.close_reason}</span>
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
        </div>
      )}
    </div>
  );
};

// ── HistoricoTab ──────────────────────────────────────────────────────────────

export const HistoricoTab: React.FC<HistoricoTabProps> = ({ customerId, tenantId }) => {
  const { t } = useTranslation('agentAssist');
  const { entries, loading, error, refetch } = useCustomerHistory(customerId);

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

      {/* Open journeys (Arc 10) */}
      <OpenJourneys tenantId={tenantId} customerId={customerId} />

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

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-3">
        {loading && entries.length === 0 && (
          <div className="flex items-center justify-center h-full">
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
      </div>
    </div>
  );
};
