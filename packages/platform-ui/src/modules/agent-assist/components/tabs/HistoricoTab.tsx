/**
 * HistoricoTab
 * Shows the customer's last N closed sessions from analytics-api.
 *
 * Each session row shows: date, channel icon, duration, outcome badge, close_reason.
 * Clicking a row expands it to show pool_id and session_id for reference.
 *
 * Note (Arc 19 Fase F): "Processos em aberto" (Open Journeys) section removed —
 * Journey entity eliminated. Session history remains unchanged.
 */

import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Timer, User } from "lucide-react";
import { ContactHistoryEntry } from "../../types";
import { useCustomerHistory } from "../../hooks/useCustomerHistory";

interface HistoricoTabProps {
  customerId:  string | null;
  tenantId?:   string | null;   // retained for API compatibility — unused after Arc 19 Fase F
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

export const HistoricoTab: React.FC<HistoricoTabProps> = ({ customerId }) => {
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
