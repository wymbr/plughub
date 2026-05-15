/**
 * CopilotBanner
 *
 * Inline copilot suggestion banner rendered between ChatArea and AgentInput.
 * Shows the AI-generated suggested response and any risk flags / recommended actions.
 *
 * Dismissed by the X button. Re-appears automatically when new copilot data arrives
 * (tracked by last_analysis timestamp in the parent).
 *
 * Design: compact single-row or two-row card; never blocks the input.
 */

import React, { useState } from "react";
import { CopilotSuggestions } from "../types";

export interface CopilotBannerProps {
  suggestions: CopilotSuggestions | null;
  /** Epoch ms of last copilot update — changing this restores the banner after dismissal */
  lastUpdate:  number;
  onDismiss?:  () => void;
}

export const CopilotBanner: React.FC<CopilotBannerProps> = ({
  suggestions,
  lastUpdate,
  onDismiss,
}) => {
  // Track which update epoch was dismissed so re-triggers re-show
  const [dismissedAt, setDismissedAt] = useState<number>(0);

  if (!suggestions) return null;

  const { sugestao_resposta, flags_risco, acoes_recomendadas } = suggestions;
  const hasContent =
    (sugestao_resposta && sugestao_resposta.trim()) ||
    flags_risco.length > 0 ||
    acoes_recomendadas.length > 0;

  if (!hasContent) return null;

  // Hidden if user dismissed this particular update epoch
  if (dismissedAt >= lastUpdate && dismissedAt > 0) return null;

  function handleDismiss() {
    setDismissedAt(lastUpdate);
    onDismiss?.();
  }

  return (
    <div className="flex-shrink-0 border-t border-indigo-100 bg-indigo-50 px-3 py-2">
      {/* ── Header row ── */}
      <div className="flex items-center justify-between mb-1">
        <span className="flex items-center gap-1 text-[10px] font-bold text-indigo-500
          uppercase tracking-wide">
          <span className="text-[11px]">✨</span>
          Sugestão do Copilot
        </span>
        <button
          onClick={handleDismiss}
          title="Dispensar sugestão"
          className="text-indigo-400 hover:text-indigo-600 text-xs leading-none"
        >
          ✕
        </button>
      </div>

      {/* ── Suggested response ── */}
      {sugestao_resposta && sugestao_resposta.trim() && (
        <p className="text-xs text-indigo-900 leading-snug line-clamp-3 mb-1.5">
          {sugestao_resposta}
        </p>
      )}

      {/* ── Risk flags ── */}
      {flags_risco.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-1">
          {flags_risco.map(f => (
            <span
              key={f}
              className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-800
                border border-amber-200 font-medium"
            >
              ⚠️ {f.replace(/_/g, " ")}
            </span>
          ))}
        </div>
      )}

      {/* ── Recommended actions ── */}
      {acoes_recomendadas.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {acoes_recomendadas.map(a => (
            <span
              key={a}
              className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700
                border border-indigo-200 font-medium"
            >
              → {a.replace(/_/g, " ")}
            </span>
          ))}
        </div>
      )}
    </div>
  );
};
