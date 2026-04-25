/**
 * ChatArea
 * Scrollable list of chat messages with auto-scroll to bottom.
 * Shows a typing indicator when agent_ai is typing.
 * Optionally renders a live sentiment strip at the top when liveState is provided.
 */

import React, { useEffect, useRef } from "react";
import { ChatMessage } from "../types";
import { MessageBubble } from "./MessageBubble";

interface LiveState {
  sentimentScore: number;   // -1 to +1
  sentimentAlert: boolean;
  sentimentTrend: "improving" | "stable" | "declining";
  intent: string | null;
  flags: string[];
}

interface ChatAreaProps {
  messages: ChatMessage[];
  aiTyping: boolean;
  /** Live AI analysis data — shown as a compact strip above the messages */
  liveState?: LiveState | null;
  /** True when the customer has disconnected — shows a disconnection banner */
  sessionClosed?: boolean;
}

const TREND_ICON: Record<string, string> = {
  improving: "↑",
  declining: "↓",
  stable:    "→",
};

function sentimentBulletColor(score: number, alert: boolean): string {
  if (alert) return "bg-red-500 animate-pulse";
  if (score >= 0.3) return "bg-green-500";
  if (score >= -0.3) return "bg-yellow-500";
  return "bg-red-500";
}

function sentimentTextColor(score: number, alert: boolean): string {
  if (alert) return "text-red-700 font-semibold";
  if (score >= 0.3) return "text-green-700";
  if (score >= -0.3) return "text-yellow-700";
  return "text-red-700";
}

function sentimentLabel(score: number): string {
  if (score >= 0.5)  return "Muito positivo";
  if (score >= 0.2)  return "Positivo";
  if (score >= -0.2) return "Neutro";
  if (score >= -0.5) return "Negativo";
  return "Muito negativo";
}

export const ChatArea: React.FC<ChatAreaProps> = ({ messages, aiTyping, liveState, sessionClosed }) => {
  const bottomRef = useRef<HTMLDivElement | null>(null);

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
      {/* ── Disconnection banner ── */}
      {sessionClosed && (
        <div className="flex items-center gap-2 px-3 py-2 bg-amber-50 border-b border-amber-200 flex-shrink-0">
          <span className="text-amber-600 text-sm">⚠️</span>
          <span className="text-amber-800 text-xs font-medium">
            Cliente desconectou — preencha o encerramento para liberar o contato.
          </span>
        </div>
      )}

      {/* ── Live sentiment strip ── */}
      {hasLiveData && (
        <div
          className={`flex items-center gap-2 px-3 py-1.5 text-xs flex-shrink-0 border-b transition-colors ${
            liveState!.sentimentAlert
              ? "bg-red-50 border-red-200"
              : "bg-white border-gray-100"
          }`}
        >
          {/* Sentiment dot + score */}
          <span
            className={`w-2 h-2 rounded-full flex-shrink-0 ${sentimentBulletColor(
              liveState!.sentimentScore,
              liveState!.sentimentAlert
            )}`}
          />
          <span className={sentimentTextColor(liveState!.sentimentScore, liveState!.sentimentAlert)}>
            {(liveState!.sentimentScore * 100).toFixed(0)}%
          </span>
          <span className="text-gray-400 text-[10px]">
            {sentimentLabel(liveState!.sentimentScore)}
          </span>
          <span className="text-gray-400 ml-0.5">
            {TREND_ICON[liveState!.sentimentTrend] ?? "→"}
          </span>

          {/* Intent */}
          {liveState!.intent && (
            <span className="text-gray-600 truncate max-w-[140px] ml-1 border-l border-gray-200 pl-2">
              {liveState!.intent}
            </span>
          )}

          {/* Flags */}
          {liveState!.flags.length > 0 && (
            <div className="flex gap-1 ml-auto">
              {liveState!.flags.slice(0, 3).map((f) => (
                <span
                  key={f}
                  className="bg-amber-100 text-amber-800 border border-amber-200 px-1.5 py-0.5 rounded text-[10px] font-medium"
                >
                  {f}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Message list ── */}
      <div className="flex-1 overflow-y-auto px-4 py-3 flex flex-col gap-3 bg-gray-50">
        {messages.length === 0 && (
          <div className="flex-1 flex items-center justify-center">
            <p className="text-sm text-gray-400">Aguardando mensagens…</p>
          </div>
        )}

        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}

        {aiTyping && (
          <div className="flex items-center gap-1 self-start bg-violet-100 text-violet-700 px-3 py-2 rounded-2xl rounded-tl-none text-xs">
            <span className="w-1.5 h-1.5 bg-violet-500 rounded-full animate-bounce [animation-delay:-0.3s]" />
            <span className="w-1.5 h-1.5 bg-violet-500 rounded-full animate-bounce [animation-delay:-0.15s]" />
            <span className="w-1.5 h-1.5 bg-violet-500 rounded-full animate-bounce" />
          </div>
        )}

        <div ref={bottomRef} />
      </div>
    </div>
  );
};
