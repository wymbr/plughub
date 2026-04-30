/**
 * Header
 * Row 1: agent name / session info / handle-time / SLA / WS status
 * Row 2 (presence bar): pool pills + "Entrar em todos" — replaces PresenceSidebar column
 */

import React, { useEffect, useState } from "react";
import { PoolInfo, PoolConnectionStatus, SlaState, WsStatus } from "../types";

interface HeaderProps {
  agentName:        string;
  poolId:           string;       // pool of selected contact (for session subtitle)
  sessionId:        string | null;
  wsStatus:         WsStatus;
  sla:              SlaState | null;
  sessionStartedAt: Date | null;
  contactCount?:    number;       // total contacts currently handled (capacity indicator)
  // Presence (pool pills)
  pools:            PoolInfo[];
  activePools:      string[];
  poolStatuses:     Map<string, PoolConnectionStatus>;
  onTogglePool:     (poolId: string) => void;
  onJoinAll:        () => void;
}

function formatElapsed(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const h   = Math.floor(totalSec / 3600);
  const m   = Math.floor((totalSec % 3600) / 60);
  const s   = totalSec % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

const STATUS_COLORS: Record<WsStatus, string> = {
  connected:    "bg-green-500",
  connecting:   "bg-yellow-400",
  disconnected: "bg-red-500",
};

// ── Pool pill ─────────────────────────────────────────────────────────────────
const CHANNEL_ICON: Record<string, string> = {
  webchat: "💬", whatsapp: "📱", voice: "📞", email: "✉️",
  sms: "📩", telegram: "✈️", instagram: "📷", webrtc: "🎙️",
};

function primaryChannelIcon(channelTypes: string[]): string {
  const first = channelTypes[0];
  return first ? (CHANNEL_ICON[first] ?? "💬") : "💬";
}

interface PoolPillProps {
  pool:    PoolInfo;
  active:  boolean;
  status:  PoolConnectionStatus | undefined;
  onToggle: () => void;
}

const PoolPill: React.FC<PoolPillProps> = ({ pool, active, status, onToggle }) => {
  const label = pool.display_name ?? pool.pool_id;
  const shortLabel = label.replace(/_/g, " ").replace(/\s*(humano|ia|v\d+)$/i, "").trim() || label;

  const dotColor =
    !active           ? "bg-gray-300" :
    status === "connected"  ? "bg-green-400" :
    status === "connecting" ? "bg-yellow-400 animate-pulse" :
                              "bg-gray-400";

  return (
    <button
      onClick={onToggle}
      title={`${label} — ${active ? "Ready (clique para Offline)" : "Offline (clique para Ready)"}`}
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-medium
        transition-colors whitespace-nowrap flex-shrink-0
        ${active
          ? "bg-indigo-50 border-indigo-300 text-indigo-700 hover:bg-indigo-100"
          : "bg-white border-gray-200 text-gray-400 hover:border-gray-300 hover:text-gray-600"
        }`}
    >
      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotColor}`} />
      <span className="text-[10px]">{primaryChannelIcon(pool.channel_types)}</span>
      <span>{shortLabel}</span>
    </button>
  );
};

// ── Main component ─────────────────────────────────────────────────────────────
export const Header: React.FC<HeaderProps> = ({
  agentName,
  poolId,
  sessionId,
  wsStatus,
  sla,
  sessionStartedAt,
  contactCount = 0,
  pools,
  activePools,
  poolStatuses,
  onTogglePool,
  onJoinAll,
}) => {
  const [handleMs, setHandleMs] = useState<number>(0);

  useEffect(() => {
    if (!sessionStartedAt) { setHandleMs(0); return; }
    setHandleMs(Date.now() - sessionStartedAt.getTime());
    const id = setInterval(() => setHandleMs(Date.now() - sessionStartedAt.getTime()), 1_000);
    return () => clearInterval(id);
  }, [sessionStartedAt]);

  const slaPercent = sla ? Math.min(sla.percentage, 100) : 0;
  const slaColor =
    !sla ? "bg-gray-300"
    : sla.breach_imminent ? "bg-red-500"
    : slaPercent > 70 ? "bg-yellow-400"
    : "bg-green-500";

  const allActive   = pools.length > 0 && pools.every(p => activePools.includes(p.pool_id));
  const activeCount = activePools.length;

  return (
    <header className="bg-white border-b border-gray-200 flex-shrink-0">
      {/* ── Row 1: identity / session / status ── */}
      <div className="px-4 py-2 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-indigo-600 flex items-center justify-center
            text-white text-sm font-semibold flex-shrink-0">
            {agentName.charAt(0).toUpperCase()}
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-gray-800 leading-tight truncate">
              {agentName}
            </p>
            <p className="text-xs text-gray-500 leading-tight truncate">
              {sessionId
                ? <>{poolId}<span className="ml-2 font-mono text-gray-400">{sessionId.slice(0, 8)}…</span></>
                : activeCount === 0
                  ? <span className="text-gray-400 italic">Offline — selecione um pool</span>
                  : <span className="text-green-600 font-medium">Ready em {activeCount} pool{activeCount > 1 ? "s" : ""}</span>
              }
            </p>
          </div>
        </div>

        <div className="flex items-center gap-4">
          {sessionStartedAt && sessionId && (
            <div className="flex items-center gap-1.5" title="Tempo de atendimento">
              <span className="text-xs text-gray-400">⏱</span>
              <span className={`text-sm font-mono font-semibold tabular-nums ${
                handleMs >= 30 * 60 * 1000 ? "text-orange-600" : "text-indigo-700"
              }`}>
                {formatElapsed(handleMs)}
              </span>
            </div>
          )}

          {sla && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-500">SLA</span>
              <div className="w-24 h-2 bg-gray-200 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${slaColor}`}
                  style={{ width: `${slaPercent}%` }}
                />
              </div>
              <span className="text-xs text-gray-600 w-10 text-right">
                {formatElapsed(sla.elapsed_ms)}
              </span>
              {sla.breach_imminent && (
                <span className="text-xs font-semibold text-red-600 animate-pulse">BREACH</span>
              )}
            </div>
          )}

          {/* Capacity indicator */}
          {contactCount > 0 && (
            <div
              className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-indigo-50
                border border-indigo-200 text-indigo-700 text-xs font-medium"
              title="Contatos em atendimento"
            >
              <span>🎧</span>
              <span>Atendendo {contactCount}</span>
            </div>
          )}

          <div className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${STATUS_COLORS[wsStatus]}`} />
            <span className="text-xs text-gray-500 capitalize">{wsStatus}</span>
          </div>
        </div>
      </div>

      {/* ── Row 2: pool presence pills ── */}
      {pools.length > 0 && (
        <div className="px-4 pb-2 flex items-center gap-2 overflow-x-auto scrollbar-none">
          {/* "Entrar em todos" — only shown when not all pools are active */}
          {!allActive && (
            <button
              onClick={onJoinAll}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full border
                bg-indigo-600 border-indigo-600 text-white text-xs font-semibold
                hover:bg-indigo-700 transition-colors whitespace-nowrap flex-shrink-0"
            >
              <span>⚡</span>
              Entrar em todos
            </button>
          )}

          {pools.map(pool => (
            <PoolPill
              key={pool.pool_id}
              pool={pool}
              active={activePools.includes(pool.pool_id)}
              status={poolStatuses.get(pool.pool_id)}
              onToggle={() => onTogglePool(pool.pool_id)}
            />
          ))}
        </div>
      )}
    </header>
  );
};
