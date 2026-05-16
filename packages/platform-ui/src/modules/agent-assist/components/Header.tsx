/**
 * Header — Arc 11 Fase 2 (Fase B)
 * Row 1: agent name / ready state / contact-count badge / pause / WS status
 * Row 2: pool combo dropdown — "X/Y Pools" button that opens a popover
 *        with per-pool toggle rows (replaces the overflow pill bar)
 *
 * Removed (Fase B): session-specific sub-line (poolId + sessionId), handle-time
 * counter, SLA bar. Those signals now live exclusively in the left contact list
 * where the agent can compare across all active sessions.
 *
 * Props poolId / sessionId / sla / sessionStartedAt are kept in the interface
 * (marked @deprecated) so the parent compiles without changes. They are no
 * longer rendered. Remove them when the parent is updated.
 */

import React, { useRef, useState, useEffect } from "react";
// useEffect kept for PoolCombo's outside-click handler
import { PoolInfo, PoolConnectionStatus, WsStatus } from "../types";

interface HeaderProps {
  agentName:        string;
  /** @deprecated — session identity now shown only in the contact list (Fase B) */
  poolId?:           string;
  /** @deprecated — session identity now shown only in the contact list (Fase B) */
  sessionId?:        string | null;
  wsStatus:         WsStatus;
  /** @deprecated — SLA now shown only in the contact list (Fase B) */
  sla?:              unknown;
  /** @deprecated — timer now shown only in the contact list and ActionBar (Fase B) */
  sessionStartedAt?: Date | null;
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
            ? "bg-white/15 border-white/30 text-white hover:bg-white/25"
            : "bg-white/8 border-white/20 text-blue-200 hover:bg-white/15"
          }`}
      >
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${comboDot}`} />
        {comboLabel}
        <span className="text-blue-300 ml-0.5">{open ? "▲" : "▼"}</span>
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
  wsStatus,
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
  const activeCount = activePools.length;

  return (
    // Primary brand colour (#1B4F8A) so the header is visually distinct from the
    // white/light-gray content columns below.
    <header className="bg-[#1B4F8A] flex-shrink-0 shadow-md">
      {/* ── Row 1: agent identity / ready state / controls / status ── */}
      <div className="px-4 py-2 flex items-center justify-between gap-3">

        {/* Left: avatar + name + ready state */}
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-8 h-8 rounded-full bg-white/20 border border-white/30
            flex items-center justify-center text-white text-sm font-semibold flex-shrink-0">
            {agentName.charAt(0).toUpperCase()}
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-white leading-tight truncate">
              {agentName}
            </p>
            <p className="text-xs leading-tight">
              {activeCount === 0
                ? <span className="text-blue-300 italic">Offline — selecione um pool</span>
                : <span className="text-green-300 font-medium">
                    Ready em {activeCount} pool{activeCount > 1 ? "s" : ""}
                  </span>
              }
            </p>
          </div>
        </div>

        {/* Right: contact-count badge / pause / WS status */}
        <div className="flex items-center gap-3 flex-shrink-0">

          {contactCount > 0 && (
            <div className="flex items-center gap-1 px-2 py-0.5 rounded-full
              bg-white/15 border border-white/25 text-white text-xs font-medium"
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
                  ? "bg-amber-400/20 border-amber-300 text-amber-200 hover:bg-amber-400/30"
                  : "bg-white/10 border-white/25 text-blue-100 hover:bg-white/20"
              }`}
            >
              <span>{isPaused ? "⏸" : "▶"}</span>
              <span>{isPaused ? "Pausado" : "Pausar"}</span>
            </button>
          )}

          <div className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${STATUS_COLORS[wsStatus]}`} />
            <span className="text-xs text-blue-200 capitalize">{wsStatus}</span>
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
