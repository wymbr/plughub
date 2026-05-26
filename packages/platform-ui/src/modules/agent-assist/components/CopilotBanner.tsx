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
import { Sparkles, X } from "lucide-react";
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
    <div className="flex-shrink-0 border-t border-ai/20 bg-ai-light px-3 py-2">
      {/* ── Header row ── */}
      <div className="flex items-center justify-between mb-1">
        <span className="flex items-center gap-1 text-2xs font-bold text-ai
          uppercase tracking-wide">
          <Sparkles className="w-3 h-3" aria-hidden="true" />
          Sugestão do Copilot
        </span>
        <button
          onClick={handleDismiss}
          title="Dispensar sugestão"
          className="text-ai hover:text-ai-text leading-none"
        >
          <X className="w-3.5 h-3.5" aria-hidden="true" />
        </button>
      </div>

      {/* ── Suggested response ── */}
      {sugestao_resposta && sugestao_resposta.trim() && (
        <p className="text-xs text-ai-text leading-snug line-clamp-3 mb-1.5">
          {sugestao_resposta}
        </p>
      )}

      {/* ── Risk flags ── */}
      {flags_risco.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-1">
          {flags_risco.map(f => (
            <span
              key={f}
              className="text-2xs px-1.5 py-0.5 rounded bg-warning-light text-warning-text
                border border-warning/30 font-medium"
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
              className="text-2xs px-1.5 py-0.5 rounded bg-ai-light text-ai-text
                border border-ai/30 font-medium"
            >
              → {a.replace(/_/g, " ")}
            </span>
          ))}
        </div>
      )}
    </div>
  );
};
