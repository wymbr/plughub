/**
 * ChatArea
 * Scrollable list of chat messages with auto-scroll to bottom.
 * Shows a typing indicator when agent_ai is typing.
 * Optionally renders a live sentiment strip at the top when liveState is provided.
 */

import React, { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { ChatMessage } from "../types";
import { MessageBubble } from "./MessageBubble";
import { SubmitResult } from "./MenuCard";
import { useMaskingDisplayRules } from "@/components/MaskedToken";

interface LiveState {
  sentimentScore: number;
  sentimentAlert: boolean;
  sentimentTrend: "improving" | "stable" | "declining";
  intent: string | null;
  flags: string[];
}

interface ChatAreaProps {
  messages:          ChatMessage[];
  aiTyping:          boolean;
  liveState?:        LiveState | null;
  sessionClosed?:    boolean;
  substitutionMode?: boolean;
  onMenuSubmit?:     (menuId: string, result: SubmitResult) => void;
  /** Arc 11 Fase C — set of selected message IDs for delegation context */
  selectedMessageIds?: Set<string>;
  /** Arc 11 Fase C — toggle a message's selection state */
  onToggleSelection?:  (messageId: string) => void;
}

const TREND_ICON: Record<string, string> = {
  improving: "↑",
  declining: "↓",
  stable:    "→",
};

function sentimentBulletColor(score: number, alert: boolean): string {
  if (alert) return "bg-red animate-pulse";
  if (score >= 0.3) return "bg-green";
  if (score >= -0.3) return "bg-warning";
  return "bg-red";
}

function sentimentTextColor(score: number, alert: boolean): string {
  if (alert) return "text-red-text font-semibold";
  if (score >= 0.3) return "text-green-text";
  if (score >= -0.3) return "text-warning-text";
  return "text-red-text";
}

// sentimentLabel is defined inside the component to access t()

export const ChatArea: React.FC<ChatAreaProps> = ({
  messages,
  aiTyping,
  liveState,
  sessionClosed,
  substitutionMode = false,
  onMenuSubmit,
  selectedMessageIds,
  onToggleSelection,
}) => {
  const { t } = useTranslation('agentAssist');
  const bottomRef    = useRef<HTMLDivElement | null>(null);
  const maskingRules = useMaskingDisplayRules();

  const sentimentLabel = (score: number): string => {
    if (score >= 0.5)  return t('estado.sentimentLabel.veryPositive');
    if (score >= 0.2)  return t('estado.sentimentLabel.positive');
    if (score >= -0.2) return t('estado.sentimentLabel.neutral');
    if (score >= -0.5) return t('estado.sentimentLabel.negative');
    return t('estado.sentimentLabel.veryNegative');
  };

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, aiTyping]);

  const hasLiveData = liveState && (
    liveState.intent !== null ||
    liveState.flags.length > 0 ||
    liveState.sentimentScore !== 0
  );

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {sessionClosed && (
        <div className="flex items-center gap-2 px-3 py-2 bg-warning-light border-b border-warning/30 flex-shrink-0">
          <span className="text-warning text-sm">⚠️</span>
          <span className="text-warning-text text-xs font-medium">
            {t('chatArea.sessionClosedBanner')}
          </span>
        </div>
      )}

      {hasLiveData && (
        <div
          role="status"
          aria-label={`Sentimento: ${sentimentLabel(liveState!.sentimentScore)}, ${(liveState!.sentimentScore * 100).toFixed(0)}%${liveState!.sentimentAlert ? ', alerta ativo' : ''}`}
          aria-live="polite"
          aria-atomic="true"
          className={`flex items-center gap-2 px-3 py-1.5 text-xs flex-shrink-0 border-b transition-colors ${
            liveState!.sentimentAlert
              ? "bg-red-light border-red/20"
              : "bg-surface border-border"
          }`}
        >
          <span
            aria-hidden="true"
            className={`w-2 h-2 rounded-full flex-shrink-0 motion-safe:${liveState!.sentimentAlert ? "animate-pulse" : ""} ${sentimentBulletColor(
              liveState!.sentimentScore,
              liveState!.sentimentAlert
            )}`}
          />
          <span aria-hidden="true" className={sentimentTextColor(liveState!.sentimentScore, liveState!.sentimentAlert)}>
            {(liveState!.sentimentScore * 100).toFixed(0)}%
          </span>
          <span aria-hidden="true" className="text-muted text-2xs">
            {sentimentLabel(liveState!.sentimentScore)}
          </span>
          <span
            aria-label={`Tendência: ${liveState!.sentimentTrend === 'improving' ? 'melhorando' : liveState!.sentimentTrend === 'declining' ? 'piorando' : 'estável'}`}
            className="text-muted ml-0.5"
          >
            {TREND_ICON[liveState!.sentimentTrend] ?? "→"}
          </span>

          {liveState!.intent && (
            <span className="text-muted truncate max-w-[140px] ml-1 border-l border-border pl-2">
              {liveState!.intent}
            </span>
          )}

          {liveState!.flags.length > 0 && (
            <div className="flex gap-1 ml-auto">
              {liveState!.flags.slice(0, 3).map((f) => (
                <span
                  key={f}
                  className="bg-warning-light text-warning-text border border-warning/30 px-1.5 py-0.5 rounded text-2xs font-medium"
                >
                  {f}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Arc 11 Fase C — selection toolbar */}
      {selectedMessageIds && selectedMessageIds.size > 0 && (
        <div className="flex items-center gap-2 px-3 py-1.5 bg-contested-light border-b border-contested/30 flex-shrink-0">
          <span className="text-2xs font-bold text-contested-text uppercase tracking-wide">
            {t('chatArea.selectionContext')}
          </span>
          <span className="text-xs text-contested">
            {t('chatArea.selectionCount', { count: selectedMessageIds.size })}
          </span>
          <span className="text-2xs text-contested/60 ml-1">
            {t('chatArea.selectionHint')}
          </span>
        </div>
      )}

      {/* role="log" + aria-live makes new messages announced to screen readers */}
      <div
        role="log"
        aria-label={t('chatArea.conversationAria')}
        aria-live="polite"
        aria-relevant="additions"
        className="flex-1 overflow-y-auto px-4 py-3 flex flex-col gap-3 bg-surface-muted"
      >
        {messages.length === 0 && (
          <div className="flex-1 flex items-center justify-center">
            <p className="text-sm text-muted">{t('chatArea.waitingMessages')}</p>
          </div>
        )}

        {messages.map((msg) => (
          <MessageBubble
            key={msg.id}
            message={msg}
            substitutionMode={substitutionMode}
            onMenuSubmit={onMenuSubmit}
            maskingRules={maskingRules}
            isSelected={selectedMessageIds?.has(msg.id) ?? false}
            onToggleSelection={onToggleSelection ? () => onToggleSelection(msg.id) : undefined}
          />
        ))}

        {aiTyping && (
          <div
            role="status"
            aria-label={t('chatArea.aiTypingAria')}
            className="flex items-center gap-1 self-start bg-ai-light text-ai px-3 py-2 rounded-2xl rounded-tl-none text-xs"
          >
            {/* Reduced-motion: dots are decorative; the role="status" announces typing */}
            <span aria-hidden="true" className="w-1.5 h-1.5 bg-ai rounded-full motion-safe:animate-bounce motion-safe:[animation-delay:-0.3s]" />
            <span aria-hidden="true" className="w-1.5 h-1.5 bg-ai rounded-full motion-safe:animate-bounce motion-safe:[animation-delay:-0.15s]" />
            <span aria-hidden="true" className="w-1.5 h-1.5 bg-ai rounded-full motion-safe:animate-bounce" />
          </div>
        )}

        <div ref={bottomRef} aria-hidden="true" />
      </div>
    </div>
  );
};
