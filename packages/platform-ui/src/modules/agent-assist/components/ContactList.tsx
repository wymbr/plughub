/**
 * ContactList
 * Left column listing active contacts, sorted by arrival time (FIFO — oldest first).
 *
 * Each row shows:
 *   - Channel icon
 *   - ANI / user_id (contactId) or short session_id fallback
 *   - Unread badge
 *   - AI-typing indicator
 *   - Live wait-time counter (from sessionStartedAt)
 *   - SLA mini-bar (when data is available)
 *   - Sentiment dot
 *   - Red tint + "encerrado" when session is closed
 */

import React, { useEffect, useState } from "react";
import { ContactSession } from "../types";

interface ContactListProps {
  contacts:          ContactSession[];
  selectedSessionId: string | null;
  aiTypingSessions:  Set<string>;
  onSelect:          (sessionId: string) => void;
}

// ── Channel icons ──────────────────────────────────────────────────────────────
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

// ── Sentiment colour ───────────────────────────────────────────────────────────
function sentimentColor(score: number | null): string {
  if (score === null) return "bg-gray-300";
  if (score >= 0.3)   return "bg-green-400";
  if (score >= -0.3)  return "bg-yellow-400";
  if (score >= -0.6)  return "bg-orange-400";
  return "bg-red-500";
}

// ── SLA urgency (for left-edge colour bar only) ────────────────────────────────
type UrgencyLevel = "low" | "medium" | "high" | "critical";

function urgencyLevel(contact: ContactSession, nowMs: number): UrgencyLevel {
  if (contact.sessionClosed) return "low";
  const waitMs = nowMs - contact.sessionStartedAt.getTime();
  const sla = contact.supervisorState?.sla?.target_ms ?? contact.slaTargetMs;
  if (!sla) return "low";
  const ratio = waitMs / sla;
  if (ratio >= 1.0) return "critical";
  if (ratio >= 0.7) return "high";
  if (ratio >= 0.4) return "medium";
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

// ── Elapsed time ───────────────────────────────────────────────────────────────
function formatElapsed(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// ── Display identity: prefer contactId (ANI/user_id), fallback to short sessionId ──
function displayId(contact: ContactSession): string {
  if (contact.contactId) return contact.contactId;
  return contact.sessionId.slice(0, 8);
}

// ── Single contact row ─────────────────────────────────────────────────────────
interface RowProps {
  contact:  ContactSession;
  selected: boolean;
  aiTyping: boolean;
  onSelect: () => void;
}

const ContactRow: React.FC<RowProps> = ({ contact, selected, aiTyping, onSelect }) => {
  const [nowMs, setNowMs] = useState<number>(Date.now());

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1_000);
    return () => clearInterval(id);
  }, []);

  const handleMs    = nowMs - contact.sessionStartedAt.getTime();
  const level       = urgencyLevel(contact, nowMs);
  const sentimentScore = contact.supervisorState?.sentiment.current ?? null;
  const sla         = contact.supervisorState?.sla ?? null;
  const slaPercent  = sla ? Math.min(sla.percentage, 100) : 0;
  const slaBarColor =
    !sla ? "bg-gray-300"
    : sla.breach_imminent ? "bg-red-500"
    : slaPercent > 70 ? "bg-yellow-400"
    : "bg-green-400";

  // Tab visual: selected row bleeds right (box-shadow covers the container's right border)
  // creating the illusion of a browser tab extending into the white central surface.
  const borderAccent = contact.sessionClosed
    ? (selected ? "#ef4444" : "#fca5a5")
    : (selected ? "#4f46e5" : URGENCY_BORDER[level].replace("border-l-", ""));

  const selectedStyle: React.CSSProperties = selected
    ? {
        backgroundColor: "#ffffff",
        // 2px white shadow to the right covers the container border at this row's height
        boxShadow: "2px 0 0 0 #ffffff",
        position:  "relative",
        zIndex:    1,
      }
    : {};

  return (
    <button
      onClick={onSelect}
      style={selectedStyle}
      className={`w-full text-left px-3 py-2.5 border-b transition-colors
        focus:outline-none focus:ring-inset focus:ring-1 focus:ring-indigo-300
        border-l-[3px]
        ${contact.sessionClosed
          ? `${selected ? "bg-white" : "bg-red-50 hover:bg-red-100"} border-b-red-100`
          : `${selected ? "bg-white" : "bg-transparent hover:bg-white/60"} border-b-gray-100`
        }
      `}
      // Inline left-border colour (urgency or selection)
      ref={el => {
        if (el) el.style.borderLeftColor = borderAccent;
      }}
    >
      {/* Row 1: channel icon + identity + unread badge */}
      <div className="flex items-center gap-1.5 min-w-0">
        <span
          className="text-base leading-none flex-shrink-0"
          title={contact.sessionClosed ? "Sessão encerrada" : contact.channel}
        >
          {contact.sessionClosed ? "🔴" : channelIcon(contact.channel)}
        </span>
        <span
          className="flex-1 text-xs font-medium text-gray-800 truncate font-mono"
          title={contact.contactId ?? contact.sessionId}
        >
          {displayId(contact)}
        </span>
        {contact.unreadCount > 0 && (
          <span className="flex-shrink-0 min-w-[1.25rem] h-5 rounded-full bg-indigo-500
            text-white text-[10px] font-bold flex items-center justify-center px-1">
            {contact.unreadCount > 99 ? "99+" : contact.unreadCount}
          </span>
        )}
      </div>

      {/* Row 2: sentiment dot + wait time + SLA mini-bar + ai typing */}
      <div className="flex items-center gap-1.5 mt-1">
        <span
          className={`w-2 h-2 rounded-full flex-shrink-0 ${sentimentColor(sentimentScore)}`}
          title={`Sentimento: ${sentimentScore?.toFixed(2) ?? "n/a"}`}
        />

        <span
          className={`text-[11px] font-mono tabular-nums flex-shrink-0
            ${contact.sessionClosed ? "text-gray-400" : URGENCY_TIMER[level]}`}
          title="Tempo em atendimento"
        >
          {formatElapsed(handleMs)}
        </span>

        {sla && (
          <div
            className="flex-1 h-1 bg-gray-200 rounded-full overflow-hidden ml-1"
            title={`SLA ${slaPercent.toFixed(0)}%`}
          >
            <div
              className={`h-full rounded-full transition-all duration-500 ${slaBarColor}`}
              style={{ width: `${slaPercent}%` }}
            />
          </div>
        )}

        {aiTyping && (
          <span className="flex-shrink-0 text-[10px] text-indigo-400 animate-pulse" title="IA digitando">
            ✦
          </span>
        )}

        {contact.sessionClosed && (
          <span className="flex-shrink-0 text-[10px] bg-red-100 text-red-600 font-semibold
            px-1.5 py-0.5 rounded border border-red-200">
            enc.
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
  // FIFO: oldest sessionStartedAt first; closed contacts always last
  const sorted = [...contacts].sort((a, b) => {
    if (a.sessionClosed !== b.sessionClosed) return a.sessionClosed ? 1 : -1;
    return a.sessionStartedAt.getTime() - b.sessionStartedAt.getTime();
  });

  return (
    <div className="flex flex-col h-full bg-gray-100">
      {/* Rows — header is rendered in the shared sub-header row of AgentAssistPage */}
      {contacts.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-2 text-gray-400 p-4">
          <span className="text-2xl">⏳</span>
          <p className="text-xs text-center leading-snug">
            Aguardando próximo atendimento…
          </p>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto">
          {sorted.map(contact => (
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
