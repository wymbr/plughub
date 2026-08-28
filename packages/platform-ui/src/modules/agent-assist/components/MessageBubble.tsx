/**
 * MessageBubble
 * Renders a single chat message with author-specific styling.
 *
 * Special cases:
 *   visibility="agents_only" — amber dashed border + "Interno" badge
 *   message.agentTypeId present — shows friendly agent name + specialist color
 *   message.menuData present — delegates to MenuCard (rich interaction preview)
 */

import React from "react";
import { AuthorType, ChatMessage } from "../types";
import { MenuCard, SubmitResult } from "./MenuCard";
import { renderWithTokens, type MaskingRulesMap } from "@/components/MaskedToken";

interface MessageBubbleProps {
  message:           ChatMessage;
  substitutionMode?: boolean;
  onMenuSubmit?:     (menuId: string, result: SubmitResult) => void;
  maskingRules?:     MaskingRulesMap;
  /** Arc 11 Fase C — whether this message is currently selected as delegation context */
  isSelected?:       boolean;
  /** Arc 11 Fase C — called when the user clicks the selection checkbox */
  onToggleSelection?: () => void;
}

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

const AUTHOR_LABELS: Record<AuthorType, string> = {
  customer:    "Cliente",
  agent_human: "Agente",
  agent_ai:    "IA",
  system:      "Sistema",
};

const BUBBLE_STYLES: Record<AuthorType, string> = {
  customer:    "bg-slate-100 text-slate-800 self-start rounded-tl-none",
  agent_human: "bg-primary text-white self-end rounded-tr-none",
  agent_ai:    "bg-ai-light text-ai-text self-start rounded-tl-none",
  system:      "bg-warning-light text-warning-text self-center text-xs italic border border-warning/30",
};

const LABEL_STYLES: Record<AuthorType, string> = {
  customer:    "text-left text-slate-400",
  agent_human: "text-right text-primary/60",
  agent_ai:    "text-left text-ai",
  system:      "text-center text-warning",
};

const SPECIALIST_BUBBLE = "bg-revised-light text-revised-text self-start rounded-tl-none border border-revised/30";
const SPECIALIST_LABEL  = "text-left text-revised";

const INTERNAL_BUBBLE =
  "bg-warning-light text-warning-text self-start rounded-tl-none border border-dashed border-warning/40";
const INTERNAL_LABEL = "text-left text-warning";

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

export const MessageBubble: React.FC<MessageBubbleProps> = ({
  message,
  substitutionMode = false,
  onMenuSubmit,
  maskingRules,
  isSelected = false,
  onToggleSelection,
}) => {
  if (message.menuData) {
    // When the menu targets the agent (targetsSelf), auto-enable interactive mode
    // so the agent can click buttons directly — no manual toggle needed.
    const effectiveSubstitution = substitutionMode || (message.menuData.targetsSelf ?? false);
    return (
      <MenuCard
        data={message.menuData}
        substitutionMode={effectiveSubstitution}
        onSubmit={onMenuSubmit
          ? (result) => onMenuSubmit(message.menuData!.menu_id, result)
          : undefined}
      />
    );
  }

  // Treat as internal when: explicit "agents_only" OR array visibility (targeted
  // participant list — e.g. wrap-up messages sent to ["human_instance_id"]).
  // Array visibility means the message targets specific participants, not the customer.
  const isInternal   = message.visibility === "agents_only"
    || Array.isArray(message.visibility);
  const isSpecialist = !isInternal && message.author === "agent_ai" && isSpecialistAgent(message.agentTypeId);
  const isRight      = !isInternal && message.author === "agent_human";

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
      : LABEL_STYLES[message.author] ?? "text-left text-muted";

  const bubbleStyle = isInternal
    ? INTERNAL_BUBBLE
    : isSpecialist
      ? SPECIALIST_BUBBLE
      : BUBBLE_STYLES[message.author] ?? "bg-surface-alt text-dark self-start";

  // Arc 11 Fase C — wrap in a row with an optional selection checkbox
  const bubble = (
    <div
      className={`flex flex-col max-w-[80%] gap-0.5 ${
        isRight ? "self-end items-end" : "self-start items-start"
      } ${isSelected ? "opacity-90 ring-1 ring-contested rounded-2xl" : ""}`}
    >
      <span className={`text-xs px-1 flex items-center gap-1 ${labelStyle}`}>
        {isInternal && (
          <span className="inline-flex items-center bg-warning-light text-warning-text rounded px-1 py-0 text-micro font-semibold leading-tight">
            Interno
          </span>
        )}
        {isSpecialist && (
          <span className="inline-flex items-center bg-revised-light text-revised-text rounded px-1 py-0 text-micro font-semibold leading-tight">
            Especialista
          </span>
        )}
        {authorLabel} · {formatTime(message.timestamp)}
      </span>
      <div className={`px-3 py-2 rounded-2xl text-sm leading-relaxed ${bubbleStyle}`}>
        {renderWithTokens(message.text, maskingRules)}
      </div>
    </div>
  );

  if (!onToggleSelection) return bubble;

  return (
    <div className={`group flex items-start gap-1.5 ${isRight ? "flex-row-reverse self-end" : "self-start"}`}>
      {/* Selection toggle — always keyboard-focusable, hover-only visually for mouse users */}
      <button
        onClick={onToggleSelection}
        aria-label={isSelected ? "Remover mensagem da seleção" : "Selecionar mensagem para delegar"}
        aria-pressed={isSelected}
        className={[
          // 44×44px minimum touch target via padding; visual indicator is smaller
          "flex-shrink-0 mt-2 w-11 h-11 rounded flex items-center justify-center",
          "transition-all duration-100",
          "focus-visible:ring-2 focus-visible:ring-contested",
          // Always show on keyboard focus; hover for mouse
          isSelected
            ? ""
            : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100",
        ].join(" ")}
      >
        <span
          aria-hidden="true"
          className={[
            "w-4 h-4 rounded border-2 flex items-center justify-center transition-colors",
            isSelected
              ? "bg-contested border-contested"
              : "border-border bg-surface hover:border-contested",
          ].join(" ")}
        >
          {isSelected && (
            <svg className="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
            </svg>
          )}
        </span>
      </button>
      {bubble}
    </div>
  );
};
