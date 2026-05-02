/**
 * ContactList
 * Left-column sidebar listing all active contacts assigned to this agent.
 *
 * Each row shows:
 *   - Channel icon (webchat / whatsapp / voice / email / sms)
 *   - Customer name (or session ID fallback)
 *   - Unread message badge
 *   - Sentiment colour indicator (if supervisorState is available)
 *   - SLA progress mini-bar
 *   - Live handle-time counter
 *
 * A "lobby" row is shown at the top when there are no contacts.
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
  if (score >= 0.3)  return "bg-green-400";
  if (score >= -0.3) return "bg-yellow-400";
  if (score >= -0.6) return "bg-orange-400";
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

// ── Single contact row ─────────────────────────────────────────────────────
interface RowProps {
  contact:   ContactSession;
  selected:  boolean;
  aiTyping:  boolean;
  onSelect:  () => void;
}

const ContactRow: React.FC<RowProps> = ({ contact, selected, aiTyping, onSelect }) => {
  const [handleMs, setHandleMs] = useState<number>(
    Date.now() - contact.sessionStartedAt.getTime()
  );

  useEffect(() => {
    const id = setInterval(() => {
      setHandleMs(Date.now() - contact.sessionStartedAt.getTime());
    }, 1_000);
    return () => clearInterval(id);
  }, [contact.sessionStartedAt]);

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

  return (
    <button
      onClick={onSelect}
      className={`w-full text-left px-3 py-2.5 border-b transition-colors
        focus:outline-none focus:ring-inset focus:ring-1 focus:ring-indigo-300
        ${contact.sessionClosed
          ? `bg-red-50 border-red-100 hover:bg-red-100
             ${selected ? "border-l-[3px] border-l-red-500" : "border-l-[3px] border-l-red-300"}`
          : `border-gray-100 hover:bg-indigo-50
             ${selected ? "bg-indigo-50 border-l-[3px] border-l-indigo-500" : "border-l-[3px] border-l-transparent"}`
        }
      `}
    >
      {/* Row 1: channel icon + name + unread badge */}
      <div className="flex items-center gap-1.5 min-w-0">
        <span className="text-base leading-none flex-shrink-0" title={contact.sessionClosed ? "Sessão encerrada" : contact.channel}>
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

      {/* Row 2: sentiment dot + handle time + SLA bar */}
      <div className="flex items-center gap-1.5 mt-1">
        {/* Sentiment dot */}
        <span
          className={`w-2 h-2 rounded-full flex-shrink-0 ${sentimentColor(sentimentScore)}`}
          title={`Sentimento: ${sentimentScore?.toFixed(2) ?? "n/a"}`}
        />

        {/* Handle time */}
        <span
          className={`text-[11px] font-mono tabular-nums flex-shrink-0
            ${handleMs >= 30 * 60 * 1000 ? "text-orange-500 font-semibold" : "text-gray-400"}`}
          title="Tempo de atendimento"
        >
          {formatElapsed(handleMs)}
        </span>

        {/* SLA mini-bar */}
        {sla && (
          <div className="flex-1 h-1 bg-gray-200 rounded-full overflow-hidden ml-1" title={`SLA ${slaPercent.toFixed(0)}%`}>
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
          <span className="flex-shrink-0 text-[10px] bg-red-100 text-red-600 font-semibold px-1.5 py-0.5 rounded border border-red-200">
            encerrado
          </span>
        )}
      </div>
    </button>
  );
};

// ── Main component ─────────────────────────────────────────────────────────
export const ContactList: React.FC<ContactListProps> = ({
  contacts,
  selectedSessionId,
  aiTypingSessions,
  onSelect,
}) => {
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
        // Lobby state
        <div className="flex-1 flex flex-col items-center justify-center gap-2 text-gray-400 p-4">
          <span className="text-2xl">⏳</span>
          <p className="text-xs text-center leading-snug">
            Aguardando próximo atendimento…
          </p>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto">
          {contacts.map((contact) => (
            <ContactRow
              key={contact.sessionId}
              contact={contact}
              selected={contact.sessionId === selectedSessionId}
              aiTyping={aiTypingSessions.has(contact.sessionId)}
              onSelect={() => onSelect(contact.sessionId)}
            />
          ))}
        </div>
      )}
    </div>
  );
};
