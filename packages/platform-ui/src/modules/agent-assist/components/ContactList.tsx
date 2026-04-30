/**
 * ContactList
 * Left-column sidebar listing all active contacts assigned to this agent,
 * sorted by urgency (highest priority first).
 *
 * Priority score = waitMs / slaTargetMs  (higher = more urgent).
 * When slaTargetMs is unknown, closed contacts always sort last.
 *
 * Each row shows:
 *   - "Próximo sugerido" badge on the top-priority open contact
 *   - Urgency color bar on the left edge (green / yellow / orange / red)
 *   - Channel icon + pool badge
 *   - Customer name (or session ID fallback)
 *   - Unread message badge
 *   - Sentiment colour indicator (if supervisorState is available)
 *   - SLA progress mini-bar
 *   - Live wait-time counter (from sessionStartedAt)
 */

import React, { useEffect, useState } from "react";
import { ContactSession } from "../types";

interface ContactListProps {
  contacts:          ContactSession[];
  selectedSessionId: string | null;
  aiTypingSessions:  Set<string>;
  onSelect:          (sessionId: string) => void;
}

// ── Channel icons (emoji fallbacks — no extra deps) ────────────────────────
const CHANNEL_ICON: Record<string, string> = {
  webchat:   "💬",
  whatsapp:  "📱",
  voice:     "📞",
  email:     "✉️",
  sms:       "📩",
  telegram:  "✈️",
  instagram: "📷",
  webrtc:    "🎙️",
};

function channelIcon(channel: string): string {
  return CHANNEL_ICON[channel] ?? "💬";
}

// ── Sentiment colour ───────────────────────────────────────────────────────
function sentimentColor(score: number | null): string {
  if (score === null) return "bg-gray-300";
  if (score >= 0.3)   return "bg-green-400";
  if (score >= -0.3)  return "bg-yellow-400";
  if (score >= -0.6)  return "bg-orange-400";
  return "bg-red-500";
}

// ── Elapsed time formatter ─────────────────────────────────────────────────
function formatElapsed(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  }
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// ── Priority / urgency ────────────────────────────────────────────────────
/**
 * Returns a score in [0, ∞).
 * score = waitMs / slaTargetMs  → 1.0 means SLA is exactly at deadline.
 * Closed contacts get Infinity so they always sort to the bottom.
 */
function urgencyScore(contact: ContactSession, nowMs: number): number {
  if (contact.sessionClosed) return Infinity;
  const waitMs = nowMs - contact.sessionStartedAt.getTime();
  const sla = contact.supervisorState?.sla?.target_ms ?? contact.slaTargetMs;
  if (!sla) return waitMs / 1;          // sort by raw wait when no SLA
  return waitMs / sla;
}

type UrgencyLevel = "low" | "medium" | "high" | "critical";

function urgencyLevel(score: number): UrgencyLevel {
  if (score >= 1.0) return "critical";
  if (score >= 0.7) return "high";
  if (score >= 0.4) return "medium";
  return "low";
}

const URGENCY_BORDER: Record<UrgencyLevel, string> = {
  low:      "border-l-green-400",
  medium:   "border-l-yellow-400",
  high:     "border-l-orange-400",
  critical: "border-l-red-500",
};

const URGENCY_TIMER: Record<UrgencyLevel, string> = {
  low:      "text-gray-400",
  medium:   "text-yellow-600",
  high:     "text-orange-500 font-semibold",
  critical: "text-red-500 font-bold",
};

// ── Pool badge ─────────────────────────────────────────────────────────────
function poolLabel(poolId: string): string {
  // Show last segment for readability: "retencao_humano" → "retencao"
  return poolId.split("_").slice(0, -1).join("_") || poolId;
}

// ── Single contact row ─────────────────────────────────────────────────────
interface RowProps {
  contact:    ContactSession;
  selected:   boolean;
  aiTyping:   boolean;
  suggested:  boolean;
  onSelect:   () => void;
}

const ContactRow: React.FC<RowProps> = ({ contact, selected, aiTyping, suggested, onSelect }) => {
  const [nowMs, setNowMs] = useState<number>(Date.now());

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1_000);
    return () => clearInterval(id);
  }, []);

  const handleMs   = nowMs - contact.sessionStartedAt.getTime();
  const score      = urgencyScore(contact, nowMs);
  const level      = contact.sessionClosed ? "low" : urgencyLevel(score);

  const sentimentScore = contact.supervisorState?.sentiment.current ?? null;
  const sla            = contact.supervisorState?.sla ?? null;
  const slaPercent     = sla ? Math.min(sla.percentage, 100) : 0;
  const slaColor       =
    !sla ? "bg-gray-300"
    : sla.breach_imminent ? "bg-red-500"
    : slaPercent > 70 ? "bg-yellow-400"
    : "bg-green-400";

  const displayName =
    contact.customerName
    ?? `#${contact.sessionId.slice(0, 8)}`;

  const borderClass = contact.sessionClosed
    ? "border-l-red-300"
    : URGENCY_BORDER[level];

  return (
    <button
      onClick={onSelect}
      className={`w-full text-left px-3 py-2 border-b transition-colors
        focus:outline-none focus:ring-inset focus:ring-1 focus:ring-indigo-300
        border-l-[3px] ${borderClass}
        ${contact.sessionClosed
          ? `bg-red-50 border-b-red-100 hover:bg-red-100
             ${selected ? "border-l-red-500" : ""}`
          : `border-b-gray-100 hover:bg-indigo-50
             ${selected ? "bg-indigo-50" : ""}`
        }
      `}
    >
      {/* Badge row: "Próximo sugerido" + pool */}
      {(suggested || contact.poolId) && (
        <div className="flex items-center gap-1 mb-1">
          {suggested && !contact.sessionClosed && (
            <span className="text-[9px] font-bold uppercase tracking-wide
              bg-indigo-500 text-white px-1.5 py-0.5 rounded-full leading-none">
              Próximo ↑
            </span>
          )}
          {contact.poolId && (
            <span className="text-[9px] font-medium text-gray-400 bg-gray-100
              px-1 py-0.5 rounded truncate max-w-[80px]"
              title={contact.poolId}
            >
              {poolLabel(contact.poolId)}
            </span>
          )}
        </div>
      )}

      {/* Row 1: channel icon + name + unread badge */}
      <div className="flex items-center gap-1.5 min-w-0">
        <span
          className="text-base leading-none flex-shrink-0"
          title={contact.sessionClosed ? "Sessão encerrada" : contact.channel}
        >
          {contact.sessionClosed ? "🔴" : channelIcon(contact.channel)}
        </span>
        <span
          className="flex-1 text-sm font-medium text-gray-800 truncate"
          title={displayName}
        >
          {displayName}
        </span>
        {contact.unreadCount > 0 && (
          <span className="flex-shrink-0 min-w-[1.25rem] h-5 rounded-full bg-indigo-500
            text-white text-[10px] font-bold flex items-center justify-center px-1">
            {contact.unreadCount > 99 ? "99+" : contact.unreadCount}
          </span>
        )}
      </div>

      {/* Row 2: sentiment dot + wait time + SLA bar */}
      <div className="flex items-center gap-1.5 mt-1">
        {/* Sentiment dot */}
        <span
          className={`w-2 h-2 rounded-full flex-shrink-0 ${sentimentColor(sentimentScore)}`}
          title={`Sentimento: ${sentimentScore?.toFixed(2) ?? "n/a"}`}
        />

        {/* Wait time */}
        <span
          className={`text-[11px] font-mono tabular-nums flex-shrink-0
            ${contact.sessionClosed ? "text-gray-400" : URGENCY_TIMER[level]}`}
          title="Tempo de espera / atendimento"
        >
          {formatElapsed(handleMs)}
        </span>

        {/* SLA mini-bar */}
        {sla && (
          <div
            className="flex-1 h-1 bg-gray-200 rounded-full overflow-hidden ml-1"
            title={`SLA ${slaPercent.toFixed(0)}%`}
          >
            <div
              className={`h-full rounded-full transition-all duration-500 ${slaColor}`}
              style={{ width: `${slaPercent}%` }}
            />
          </div>
        )}

        {/* AI typing indicator */}
        {aiTyping && (
          <span
            className="flex-shrink-0 text-[10px] text-indigo-400 animate-pulse"
            title="IA digitando"
          >
            ✦
          </span>
        )}

        {/* Closed badge */}
        {contact.sessionClosed && (
          <span className="flex-shrink-0 text-[10px] bg-red-100 text-red-600 font-semibold
            px-1.5 py-0.5 rounded border border-red-200">
            encerrado
          </span>
        )}
      </div>
    </button>
  );
};

// ── Main component ─────────────────────────────────────────────────────────────
export const ContactList: React.FC<ContactListProps> = ({
  contacts,
  selectedSessionId,
  aiTypingSessions,
  onSelect,
}) => {
  const now = Date.now();

  // Sort: open contacts by urgency score desc, closed always last
  const sorted = [...contacts].sort((a, b) => {
    const sa = urgencyScore(a, now);
    const sb = urgencyScore(b, now);
    return sb - sa;
  });

  // The top open contact gets the "Próximo sugerido" badge
  const suggestedId = sorted.find(c => !c.sessionClosed)?.sessionId ?? null;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-3 py-2 border-b border-gray-200 bg-gray-50 flex-shrink-0">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          Contatos
        </span>
        {contacts.length > 0 && (
          <span className="ml-1.5 text-xs text-gray-400">({contacts.length})</span>
        )}
      </div>

      {/* Contact rows */}
      {contacts.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-2 text-gray-400 p-4">
          <span className="text-2xl">⏳</span>
          <p className="text-xs text-center leading-snug">
            Aguardando próximo atendimento…
          </p>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto">
          {sorted.map((contact) => (
            <ContactRow
              key={contact.sessionId}
              contact={contact}
              selected={contact.sessionId === selectedSessionId}
              aiTyping={aiTypingSessions.has(contact.sessionId)}
              suggested={contact.sessionId === suggestedId}
              onSelect={() => onSelect(contact.sessionId)}
            />
          ))}
        </div>
      )}
    </div>
  );
};
