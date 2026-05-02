/**
 * MessageBubble
 * Renders a single chat message with author-specific styling.
 *
 * Special cases:
 *   visibility="agents_only" — amber dashed border + "Interno" badge
 *   message.agentTypeId present — shows friendly agent name + specialist color for non-primary agents
 *   message.menuData present — delegates to MenuCard (rich interaction preview)
 *
 * Color palette by message type:
 *   Customer     — slate/gray    — left
 *   Human agent  — indigo        — right
 *   IA principal (sac, demo)  — violet  — left
 *   Especialista (copilot, contexto, auth) — teal — left
 *   Interno (agents_only) — amber dashed — left (overrides above)
 *   Sistema      — amber border  — center
 */

import React from "react";
import { AuthorType, ChatMessage } from "../types";
import { MenuCard } from "./MenuCard";

interface MessageBubbleProps {
  message: ChatMessage;
}

// ── Agent type → friendly label ────────────────────────────────────────────────
// Converts agent_type_id to a human-readable short label.
// Pattern: strips "agente_" prefix and "_v{n}" suffix.
function agentLabel(agentTypeId: string | undefined): string {
  if (!agentTypeId) return "IA";
  const strip = agentTypeId.replace(/^agente_/, "").replace(/_v\d+$/, "");
  const map: Record<string, string> = {
    copilot:   "Co-pilot",
    sac_ia:    "SAC",
    demo_ia:   "IVR",
    contexto_ia: "Contexto",
    auth_ia:   "Auth",
    fila:      "Fila",
    avaliacao: "Avaliação",
  };
  return map[strip] ?? strip.replace(/_/g, " ");
}

// ── Is this a specialist/secondary AI agent? ───────────────────────────────────
// Specialist agents are differentiated visually from the main SAC agent.
function isSpecialistAgent(agentTypeId: string | undefined): boolean {
  if (!agentTypeId) return false;
  const specialistPrefixes = [
    "agente_copilot",
    "agente_contexto",
    "agente_auth",
    "agente_avaliacao",
    "agente_fila",
  ];
  return specialistPrefixes.some(p => agentTypeId.startsWith(p));
}

// ── Palette ────────────────────────────────────────────────────────────────────

const AUTHOR_LABELS: Record<AuthorType, string> = {
  customer:    "Cliente",
  agent_human: "Agente",
  agent_ai:    "IA",
  system:      "Sistema",
};

const BUBBLE_STYLES: Record<AuthorType, string> = {
  customer:    "bg-slate-100 text-slate-800 self-start rounded-tl-none",
  agent_human: "bg-indigo-600 text-white self-end rounded-tr-none",
  agent_ai:    "bg-violet-100 text-violet-900 self-start rounded-tl-none",
  system:      "bg-amber-50 text-amber-800 self-center text-xs italic border border-amber-200",
};

const LABEL_STYLES: Record<AuthorType, string> = {
  customer:    "text-left text-slate-400",
  agent_human: "text-right text-indigo-400",
  agent_ai:    "text-left text-violet-500",
  system:      "text-center text-amber-600",
};

// Specialist agent overrides (teal palette)
const SPECIALIST_BUBBLE = "bg-teal-50 text-teal-900 self-start rounded-tl-none border border-teal-200";
const SPECIALIST_LABEL  = "text-left text-teal-600";

// Internal (agents_only) overrides — amber dashed, always left
const INTERNAL_BUBBLE =
  "bg-amber-50 text-amber-900 self-start rounded-tl-none border border-dashed border-amber-400";
const INTERNAL_LABEL = "text-left text-amber-600";

// ── Helpers ────────────────────────────────────────────────────────────────────

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("pt-BR", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

// ── Component ─────────────────────────────────────────────────────────────────

export const MessageBubble: React.FC<MessageBubbleProps> = ({ message }) => {
  if (message.menuData) {
    return <MenuCard data={message.menuData} />;
  }

  const isInternal   = message.visibility === "agents_only";
  const isSpecialist = !isInternal && message.author === "agent_ai" && isSpecialistAgent(message.agentTypeId);
  const isRight      = !isInternal && message.author === "agent_human";

  // Derive label
  let authorLabel: string;
  if (message.author === "agent_ai") {
    authorLabel = agentLabel(message.agentTypeId);
  } else {
    authorLabel = AUTHOR_LABELS[message.author] ?? message.author;
  }

  const labelStyle = isInternal
    ? INTERNAL_LABEL
    : isSpecialist
      ? SPECIALIST_LABEL
      : LABEL_STYLES[message.author] ?? "text-left text-gray-500";

  const bubbleStyle = isInternal
    ? INTERNAL_BUBBLE
    : isSpecialist
      ? SPECIALIST_BUBBLE
      : BUBBLE_STYLES[message.author] ?? "bg-gray-100 text-gray-800 self-start";

  return (
    <div
      className={`flex flex-col max-w-[80%] gap-0.5 ${
        isRight ? "self-end items-end" : "self-start items-start"
      }`}
    >
      <span className={`text-[10px] px-1 flex items-center gap-1 ${labelStyle}`}>
        {isInternal && (
          <span className="inline-flex items-center bg-amber-200 text-amber-800 rounded px-1 py-0 text-[9px] font-semibold leading-tight">
            Interno
          </span>
        )}
        {isSpecialist && (
          <span className="inline-flex items-center bg-teal-200 text-teal-800 rounded px-1 py-0 text-[9px] font-semibold leading-tight">
            Especialista
          </span>
        )}
        {authorLabel} · {formatTime(message.timestamp)}
      </span>
      <div className={`px-3 py-2 rounded-2xl text-sm leading-relaxed ${bubbleStyle}`}>
        {message.text}
      </div>
    </div>
  );
};
