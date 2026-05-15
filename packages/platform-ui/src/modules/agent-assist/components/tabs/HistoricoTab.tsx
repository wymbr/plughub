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

const JOURNEY_STATUS_LABELS: Record<JourneyStatus, string> = {
  active:    'Ativo',
  suspended: 'Suspenso',
  completed: 'Concluído',
  failed:    'Falhou',
  cancelled: 'Cancelado',
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
  const { journeys, loading } = useCustomerJourneys(tenantId, customerId)

  if (!loading && journeys.length === 0) return null

  return (
    <div className="px-3 pt-2 pb-0 flex-shrink-0">
      <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1.5">
        Processos em aberto
      </div>
      {loading && (
        <div className="text-xs text-gray-400 animate-pulse py-1">Carregando…</div>
      )}
      {!loading && journeys.map(j => {
        const color = JOURNEY_STATUS_COLORS[j.status]
        return (
          <div key={j.journey_id}
            className="flex items-start justify-between gap-2 px-2.5 py-1.5 rounded-lg border mb-1.5"
            style={{ borderColor: color + '40', background: color + '10' }}>
            <div className="flex-1 min-w-0">
              <div className="text-[11px] font-mono text-gray-600 truncate font-medium">
                {j.skill_id.replace(/^skill_|_v\d+$/g, '').replace(/_/g, ' ')}
              </div>
              <div className="text-[10px] text-gray-400 mt-0.5">
                {j.session_count} sessão{j.session_count !== 1 ? 'ões' : ''}
              </div>
            </div>
            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded flex-shrink-0"
              style={{ background: color + '25', color }}>
              {JOURNEY_STATUS_LABELS[j.status]}
            </span>
          </div>
        )
      })}
      <div className="border-b border-gray-100 mt-1 mb-2" />
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
    return new Date(iso).toLocaleString("pt-BR", {
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
  const map: Record<string, { label: string; color: string }> = {
    resolved:  { label: "Resolvido",  color: "bg-green-100 text-green-800" },
    escalated: { label: "Escalado",   color: "bg-yellow-100 text-yellow-800" },
    abandoned: { label: "Abandonado", color: "bg-red-100 text-red-800" },
  };
  const def = map[outcome ?? ""] ?? { label: outcome ?? "—", color: "bg-gray-100 text-gray-700" };
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${def.color}`}>
      {def.label}
    </span>
  );
}

// ── Entry row ─────────────────────────────────────────────────────────────────

const HistoryRow: React.FC<{ entry: ContactHistoryEntry }> = ({ entry }) => {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      {/* Summary row */}
      <button
        className="w-full text-left px-3 py-2 flex items-center gap-2 hover:bg-gray-50 transition-colors"
        onClick={() => setExpanded((e) => !e)}
      >
        <span className="text-base shrink-0" aria-hidden>
          {channelIcon(entry.channel)}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <OutcomeBadge outcome={entry.outcome} />
            <span className="text-xs text-gray-600 truncate">
              {formatDate(entry.opened_at)}
            </span>
          </div>
          <div className="text-[11px] text-gray-400 mt-0.5 flex gap-2">
            <span>⏱ {formatDuration(entry.duration_ms)}</span>
            {entry.close_reason && (
              <span className="truncate">{entry.close_reason}</span>
            )}
          </div>
        </div>
        <span className="text-gray-400 text-xs shrink-0">
          {expanded ? "▲" : "▼"}
        </span>
      </button>

      {/* Expanded details */}
      {expanded && (
        <div className="px-3 pb-2 pt-0 bg-gray-50 border-t border-gray-100 text-[11px] text-gray-500 space-y-1">
          <div>
            <span className="font-medium text-gray-600">Pool:</span>{" "}
            {entry.pool_id || "—"}
          </div>
          <div>
            <span className="font-medium text-gray-600">Canal:</span>{" "}
            {entry.channel}
          </div>
          {entry.closed_at && (
            <div>
              <span className="font-medium text-gray-600">Encerrado:</span>{" "}
              {formatDate(entry.closed_at)}
            </div>
          )}
          <div className="font-mono text-[10px] text-gray-400 truncate">
            {entry.session_id}
          </div>
        </div>
      )}
    </div>
  );
};

// ── HistoricoTab ──────────────────────────────────────────────────────────────

export const HistoricoTab: React.FC<HistoricoTabProps> = ({ customerId, tenantId }) => {
  const { entries, loading, error, refetch } = useCustomerHistory(customerId);

  if (!customerId) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-sm text-gray-400 p-4 gap-2">
        <span className="text-2xl">👤</span>
        <span>Cliente não identificado nesta sessão.</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* Open journeys (Arc 10) */}
      <OpenJourneys tenantId={tenantId} customerId={customerId} />

      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100 flex-shrink-0">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          Contatos anteriores
        </span>
        <button
          onClick={refetch}
          disabled={loading}
          className="text-xs text-indigo-600 hover:text-indigo-800 disabled:opacity-50 transition-colors"
          title="Recarregar histórico"
        >
          {loading ? "…" : "↻"}
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-3">
        {loading && entries.length === 0 && (
          <div className="flex items-center justify-center h-full">
            <span className="text-sm text-gray-400 animate-pulse">
              Carregando histórico…
            </span>
          </div>
        )}

        {error && (
          <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded p-2">
            Erro ao carregar histórico: {error}
          </div>
        )}

        {!loading && !error && entries.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-sm text-gray-400 gap-1">
            <span className="text-xl">🗂</span>
            <span>Sem contatos anteriores registrados.</span>
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
