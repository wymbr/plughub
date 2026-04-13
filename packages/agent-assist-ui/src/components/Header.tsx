/**
 * Header
 * Shows agent name, pool, session ID, WS connection status and SLA progress bar.
 */

import React from "react";
import { SlaState, WsStatus } from "../types";

interface HeaderProps {
  agentName: string;
  poolId: string;
  sessionId: string | null;
  wsStatus: WsStatus;
  sla: SlaState | null;
}

function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}:${rem.toString().padStart(2, "0")}`;
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
  sla,
}) => {
  const slaPercent = sla ? Math.min(sla.percentage, 100) : 0;
  const slaColor =
    !sla ? "bg-gray-300"
    : sla.breach_imminent ? "bg-red-500"
    : slaPercent > 70 ? "bg-yellow-400"
    : "bg-green-500";

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

        {/* Right: WS status + SLA */}
        <div className="flex items-center gap-4">
          {/* SLA bar */}
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
                <span className="text-xs font-semibold text-red-600 animate-pulse">
                  BREACH
                </span>
              )}
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
