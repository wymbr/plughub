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
  /** `null` = NÃO MEDIDO. Ver SentimentState.current em ../types. */
  sentimentScore: number | null;
  sentimentAlert: boolean;
  sentimentTrend: "improving" | "stable" | "declining" | null;
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
  /**
   * Motivo pelo qual o histórico persistido não pôde ser lido (`null` = leitura
   * OK). Não-nulo troca o vazio "aguardando mensagens" por uma recusa nomeada, e
   * marca a conversa como possivelmente INCOMPLETA quando já há mensagens vivas —
   * as duas coisas eram indistinguíveis até 2026-09-01. Ver `../api.ts`.
   */
  historyError?:       string | null;
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
  historyError = null,
}) => {
  const { t } = useTranslation('agentAssist');
  const bottomRef    = useRef<HTMLDivElement | null>(null);
  const scrollRef    = useRef<HTMLDivElement | null>(null);
  const maskingRules = useMaskingDisplayRules();

  const sentimentLabel = (score: number): string => {
    if (score >= 0.5)  return t('estado.sentimentLabel.veryPositive');
    if (score >= 0.2)  return t('estado.sentimentLabel.positive');
    if (score >= -0.2) return t('estado.sentimentLabel.neutral');
    if (score >= -0.5) return t('estado.sentimentLabel.negative');
    return t('estado.sentimentLabel.veryNegative');
  };

  // Auto-scroll rolando APENAS o container de mensagens (scrollRef) — nunca a
  // window. scrollIntoView rolava todos os ancestrais roláveis (incl. a página),
  // deslocando o layout da Console ao atribuir um contato / chegar mensagem.
  useEffect(() => {
    const c = scrollRef.current;
    if (c) c.scrollTo({ top: c.scrollHeight, behavior: "smooth" });
  }, [messages, aiTyping]);

  // `sentimentScore !== 0` era uma guarda de instinto correto e critério errado:
  // enquanto o backend mandava `0` para "não medido", ela protegia por acidente —
  // e escondia um `0.0` MEDIDO de verdade, que é cliente neutro legítimo. Com
  // `null` = não medido, o critério passa a ser o discriminador real.
  const sentimentScore = liveState?.sentimentScore ?? null;
  const sentimentTrend = liveState?.sentimentTrend ?? null;
  const hasLiveData = liveState && (
    liveState.intent !== null ||
    liveState.flags.length > 0 ||
    sentimentScore !== null
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
          aria-label={
            sentimentScore !== null
              ? `Sentimento: ${sentimentLabel(sentimentScore)}, ${(sentimentScore * 100).toFixed(0)}%${liveState!.sentimentAlert ? ', alerta ativo' : ''}`
              : 'Estado da conversa'
          }
          aria-live="polite"
          aria-atomic="true"
          className={`flex items-center gap-2 px-3 py-1.5 text-xs flex-shrink-0 border-b transition-colors ${
            liveState!.sentimentAlert
              ? "bg-red-light border-red/20"
              : "bg-surface border-border"
          }`}
        >
          {/* Sem medição, nada de sentimento é renderizado — a faixa continua
              existindo para intent/flags, que são medidos por outro caminho. */}
          {sentimentScore !== null && (
            <>
              <span
                aria-hidden="true"
                className={`w-2 h-2 rounded-full flex-shrink-0 motion-safe:${liveState!.sentimentAlert ? "animate-pulse" : ""} ${sentimentBulletColor(
                  sentimentScore,
                  liveState!.sentimentAlert
                )}`}
              />
              <span aria-hidden="true" className={sentimentTextColor(sentimentScore, liveState!.sentimentAlert)}>
                {(sentimentScore * 100).toFixed(0)}%
              </span>
              <span aria-hidden="true" className="text-muted text-2xs">
                {sentimentLabel(sentimentScore)}
              </span>
            </>
          )}
          {sentimentTrend !== null && (
            <span
              aria-label={`Tendência: ${sentimentTrend === 'improving' ? 'melhorando' : sentimentTrend === 'declining' ? 'piorando' : 'estável'}`}
              className="text-muted ml-0.5"
            >
              {TREND_ICON[sentimentTrend]}
            </span>
          )}

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
        ref={scrollRef}
        aria-live="polite"
        aria-relevant="additions"
        className="flex-1 overflow-y-auto px-4 py-3 flex flex-col gap-3 bg-surface-muted"
      >
        {/* Histórico ilegível: a recusa é NOMEADA, nunca uma tela vazia. Com
            mensagens vivas na tela o aviso vira faixa — a conversa está
            incompleta no começo, e é isso que o operador precisa saber antes de
            responder. */}
        {historyError && messages.length > 0 && (
          <div
            role="status"
            className="self-stretch flex items-start gap-2 px-3 py-2 rounded-lg bg-warning-light border border-warning/30 text-xs text-warning-text"
          >
            <span aria-hidden="true">⚠</span>
            <span>{t('chatArea.historyErrorPartial', { reason: historyError })}</span>
          </div>
        )}

        {messages.length === 0 && (
          <div className="flex-1 flex items-center justify-center px-6">
            {historyError ? (
              <div role="status" className="text-center">
                <p className="text-sm text-warning-text font-medium">
                  {t('chatArea.historyErrorTitle')}
                </p>
                <p className="text-xs text-muted mt-1">
                  {t('chatArea.historyErrorHint', { reason: historyError })}
                </p>
              </div>
            ) : (
              <p className="text-sm text-muted">{t('chatArea.waitingMessages')}</p>
            )}
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
