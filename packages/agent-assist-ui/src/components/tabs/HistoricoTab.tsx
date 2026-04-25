/**
 * HistoricoTab
 * Shows the customer's last N closed sessions from analytics-api.
 *
 * Each row shows: date, channel icon, duration, outcome badge, close_reason.
 * Clicking a row expands it to show pool_id and session_id for reference.
 */

import React, { useState } from "react";
import { ContactHistoryEntry } from "../../types";
import { useCustomerHistory } from "../../hooks/useCustomerHistory";

interface HistoricoTabProps {
  customerId: string | null;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function channelIcon(channel: string): string {
  switch (channel) {
    case "webchat":   return "💬";
    case "whatsapp":  return "📱";
    case "voice":     return "📞";
    case "email":     return "✉️";
    case "sms":       return "💬";
    default:          return "🔗";
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("pt-BR", {
      day:   "2-digit",
      month: "2-digit",
      year:  "numeric",
      hour:  "2-digit",
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

function outcomeBadge(outcome: string | null): JSX.Element {
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
    <div
      className="border border-gray-200 rounded-lg overflow-hidden"
    >
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
            {outcomeBadge(entry.outcome)}
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

export const HistoricoTab: React.FC<HistoricoTabProps> = ({ customerId }) => {
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
