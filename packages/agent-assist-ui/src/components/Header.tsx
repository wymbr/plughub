/**
 * Header
 * Shows agent name, pool, session ID, WS connection status, and a live
 * handle-time counter (elapsed since conversation.assigned).
 *
 * A barra de SLA saiu na D14.1 (2026-08-24) — ver `TODO.md` § Analytics e UI.
 */

import React, { useEffect, useState } from "react";
import { WsStatus } from "../types";

interface HeaderProps {
  agentName: string;
  poolId: string;
  sessionId: string | null;
  wsStatus: WsStatus;
  /** Timestamp at which the current conversation was assigned to this agent. */
  sessionStartedAt: Date | null;
}

/** Format a duration in ms as M:SS or H:MM:SS. */
function formatElapsed(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const h   = Math.floor(totalSec / 3600);
  const m   = Math.floor((totalSec % 3600) / 60);
  const s   = totalSec % 60;
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  }
  return `${m}:${s.toString().padStart(2, "0")}`;
}

const STATUS_COLORS: Record<WsStatus, string> = {
  connected: "bg-green-500",
  connecting: "bg-yellow-400",
  disconnected: "bg-red-500",
};

export const Header: React.FC<HeaderProps> = ({
  agentName,
  poolId,
  sessionId,
  wsStatus,
  sessionStartedAt,
}) => {
  // Live handle-time counter — ticks every second while a session is active.
  const [handleMs, setHandleMs] = useState<number>(0);

  useEffect(() => {
    if (!sessionStartedAt) {
      setHandleMs(0);
      return;
    }
    // Compute immediately so there's no 1-second blank on load.
    setHandleMs(Date.now() - sessionStartedAt.getTime());
    const id = setInterval(() => {
      setHandleMs(Date.now() - sessionStartedAt.getTime());
    }, 1_000);
    return () => clearInterval(id);
  }, [sessionStartedAt]);

  // Barra de SLA removida na D14.1 (2026-08-24) — ver `TODO.md` § Analytics e UI.

  return (
    <header className="bg-white border-b border-gray-200 px-4 py-2 flex-shrink-0">
      <div className="flex items-center justify-between">
        {/* Left: agent info */}
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-indigo-600 flex items-center justify-center text-white text-sm font-semibold">
            {agentName.charAt(0).toUpperCase()}
          </div>
          <div>
            <p className="text-sm font-semibold text-gray-800 leading-tight">
              {agentName}
            </p>
            <p className="text-xs text-gray-500 leading-tight">
              {poolId}
              {sessionId && (
                <span className="ml-2 font-mono text-gray-400">
                  {sessionId.slice(0, 8)}…
                </span>
              )}
            </p>
          </div>
        </div>

        {/* Right: handle time + SLA + WS status */}
        <div className="flex items-center gap-4">
          {/* Handle-time counter — only shown during an active session */}
          {sessionStartedAt && sessionId && (
            <div className="flex items-center gap-1.5" title="Tempo de atendimento">
              <span className="text-xs text-gray-400">⏱</span>
              <span
                className={`text-sm font-mono font-semibold tabular-nums ${
                  handleMs >= 30 * 60 * 1000   // warn after 30 min
                    ? "text-orange-600"
                    : "text-indigo-700"
                }`}
              >
                {formatElapsed(handleMs)}
              </span>
            </div>
          )}

          {/* WS status dot */}
          <div className="flex items-center gap-1.5">
            <span
              className={`w-2 h-2 rounded-full ${STATUS_COLORS[wsStatus]}`}
            />
            <span className="text-xs text-gray-500 capitalize">{wsStatus}</span>
          </div>
        </div>
      </div>
    </header>
  );
};
