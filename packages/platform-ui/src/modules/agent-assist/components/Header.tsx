/**
 * Header
 * Row 1: agent name / session info / handle-time / SLA / WS status
 * Row 2: pool combo dropdown — "X/Y Pools" button that opens a popover
 *        with per-pool toggle rows (replaces the overflow pill bar)
 */

import React, { useEffect, useRef, useState } from "react";
import { PoolInfo, PoolConnectionStatus, SlaState, WsStatus } from "../types";

interface HeaderProps {
  agentName:        string;
  poolId:           string;
  sessionId:        string | null;
  wsStatus:         WsStatus;
  sla:              SlaState | null;
  sessionStartedAt: Date | null;
  contactCount?:    number;
  pools:            PoolInfo[];
  activePools:      string[];
  poolStatuses:     Map<string, PoolConnectionStatus>;
  onTogglePool:     (poolId: string) => void;
  onJoinAll:        () => void;
  onLeaveAll:       () => void;
  isPaused?:        boolean;
  onTogglePause?:   () => void;   // resume path (direct, no modal)
  onPauseRequest?:  () => void;   // pause path (intercepted by PauseReasonModal)
}

function formatElapsed(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

const STATUS_COLORS: Record<WsStatus, string> = {
  connected:    "bg-green-500",
  connecting:   "bg-yellow-400",
  disconnected: "bg-red-500",
};

const CHANNEL_ICON: Record<string, string> = {
  webchat: "💬", whatsapp: "📱", voice: "📞", email: "✉️",
  sms: "📩", telegram: "✈️", instagram: "📷", webrtc: "🎙️",
};

function primaryChannelIcon(channelTypes: string[]): string {
  return CHANNEL_ICON[channelTypes[0] ?? ""] ?? "💬";
}

function shortPoolLabel(pool: PoolInfo): string {
  const label = pool.display_name ?? pool.pool_id;
  return label.replace(/_/g, " ").replace(/\s*(humano|ia|v\d+)$/i, "").trim() || label;
}

// ── Pool combo button + popover ───────────────────────────────────────────────
interface PoolComboProps {
  pools:        PoolInfo[];
  activePools:  string[];
  poolStatuses: Map<string, PoolConnectionStatus>;
  onToggle:     (poolId: string) => void;
  onJoinAll:    () => void;
  onLeaveAll:   () => void;
}

const PoolCombo: React.FC<PoolComboProps> = ({
  pools, activePools, poolStatuses, onToggle, onJoinAll, onLeaveAll,
}) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const activeCount  = activePools.length;
  const totalCount   = pools.length;
  const allActive    = totalCount > 0 && activeCount === totalCount;

  // Aggregate color: green if any connected, yellow if any connecting, gray otherwise
  const anyConnected  = activePools.some(p => poolStatuses.get(p) === "connected");
  const anyConnecting = activePools.some(p => poolStatuses.get(p) === "connecting");
  const comboDot =
    anyConnected  ? "bg-green-500" :
    anyConnecting ? "bg-yellow-400 animate-pulse" :
                    "bg-gray-300";

  const comboLabel =
    totalCount === 0 ? "Sem pools" :
    activeCount === 0 ? "Offline" :
    `${activeCount}/${totalCount} Pool${totalCount > 1 ? "s" : ""}`;

  return (
    <div ref={ref} className="relative flex-shrink-0">
      <button
        onClick={() => setOpen(prev => !prev)}
        className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full border text-xs font-medium
          transition-colors whitespace-nowrap
          ${activeCount > 0
            ? "bg-indigo-50 border-indigo-300 text-indigo-700 hover:bg-indigo-100"
            : "bg-white border-gray-200 text-gray-500 hover:border-gray-300"
          }`}
      >
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${comboDot}`} />
        {comboLabel}
        <span className="text-gray-400 ml-0.5">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1 z-50 bg-white rounded-lg shadow-lg border border-gray-200
          min-w-[220px] py-1 overflow-hidden">

          {/* Header */}
          <div className="px-3 py-1.5 border-b border-gray-100">
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
              Pools ({totalCount})
            </span>
          </div>

          {/* "Todos os pools" row */}
          {totalCount > 0 && (
            <button
              onClick={() => allActive ? onLeaveAll() : onJoinAll()}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-left hover:bg-indigo-50
                transition-colors border-b border-gray-100"
            >
              <div className={`w-4 h-4 rounded flex items-center justify-center flex-shrink-0
                transition-colors ${allActive ? "bg-indigo-600" : activeCount > 0 ? "bg-indigo-300" : "border border-gray-300 bg-white"}`}>
                {allActive
                  ? <span className="text-white text-[10px] leading-none">✓</span>
                  : activeCount > 0
                    ? <span className="text-white text-[10px] leading-none">−</span>
                    : null
                }
              </div>
              <span className="text-xs">🌐</span>
              <span className={`flex-1 text-xs font-semibold ${allActive ? "text-indigo-700" : "text-gray-600"}`}>
                Todos os pools
              </span>
              {activeCount > 0 && !allActive && (
                <span className="text-[10px] text-gray-400">{activeCount}/{totalCount}</span>
              )}
            </button>
          )}

          {/* Pool rows */}
          {pools.length === 0 && (
            <div className="px-3 py-3 text-xs text-gray-400 italic">
              Nenhum pool disponível
            </div>
          )}
          {pools.map(pool => {
            const active  = activePools.includes(pool.pool_id);
            const status  = poolStatuses.get(pool.pool_id);
            const dotColor =
              !active             ? "bg-gray-200" :
              status === "connected"  ? "bg-green-400" :
              status === "connecting" ? "bg-yellow-400 animate-pulse" :
                                        "bg-gray-300";

            return (
              <button
                key={pool.pool_id}
                onClick={() => onToggle(pool.pool_id)}
                className="w-full flex items-center gap-2.5 px-3 py-2 text-left hover:bg-gray-50
                  transition-colors text-sm"
              >
                {/* Toggle checkbox visual */}
                <div className={`w-4 h-4 rounded flex items-center justify-center flex-shrink-0
                  transition-colors ${active ? "bg-indigo-600" : "border border-gray-300 bg-white"}`}>
                  {active && <span className="text-white text-[10px] leading-none">✓</span>}
                </div>

                <span className="text-xs">{primaryChannelIcon(pool.channel_types)}</span>

                <span className={`flex-1 text-xs truncate ${active ? "text-gray-800 font-medium" : "text-gray-500"}`}>
                  {shortPoolLabel(pool)}
                </span>

                <span className={`w-2 h-2 rounded-full flex-shrink-0 ${dotColor}`}
                  title={active ? (status ?? "offline") : "offline"} />
              </button>
            );
          })}
        </div>
      )}
    </div>
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
  onLeaveAll,
  isPaused = false,
  onTogglePause,
  onPauseRequest,
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
    !sla            ? "bg-gray-300" :
    sla.breach_imminent ? "bg-red-500" :
    slaPercent > 70 ? "bg-yellow-400" :
                      "bg-green-500";

  const activeCount = activePools.length;

  return (
    <header className="bg-white border-b border-gray-200 flex-shrink-0">
      {/* ── Row 1: identity / session / status ── */}
      <div className="px-4 py-2 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
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

        <div className="flex items-center gap-4 flex-shrink-0">
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

          {contactCount > 0 && (
            <div className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-indigo-50
              border border-indigo-200 text-indigo-700 text-xs font-medium"
              title="Contatos em atendimento">
              <span>🎧</span>
              <span>Atendendo {contactCount}</span>
            </div>
          )}

          {(onTogglePause || onPauseRequest) && (
            <button
              onClick={isPaused ? onTogglePause : onPauseRequest ?? onTogglePause}
              title={isPaused ? "Retomar recebimento de contatos" : "Pausar recebimento de contatos"}
              className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full border text-xs font-medium
                transition-colors whitespace-nowrap ${
                isPaused
                  ? "bg-amber-50 border-amber-400 text-amber-700 hover:bg-amber-100"
                  : "bg-white border-gray-200 text-gray-500 hover:border-gray-300 hover:text-gray-700"
              }`}
            >
              <span>{isPaused ? "⏸" : "▶"}</span>
              <span>{isPaused ? "Pausado" : "Pausar"}</span>
            </button>
          )}

          <div className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${STATUS_COLORS[wsStatus]}`} />
            <span className="text-xs text-gray-500 capitalize">{wsStatus}</span>
          </div>
        </div>
      </div>

      {/* ── Row 2: pool combo ── */}
      {pools.length > 0 && (
        <div className="px-4 pb-2">
          <PoolCombo
            pools={pools}
            activePools={activePools}
            poolStatuses={poolStatuses}
            onToggle={onTogglePool}
            onJoinAll={onJoinAll}
            onLeaveAll={onLeaveAll}
          />
        </div>
      )}
    </header>
  );
};
