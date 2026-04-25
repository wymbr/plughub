/**
 * MessageBubble
 * Renders a single chat message with author-specific styling.
 *
 * Special cases:
 *   visibility="agents_only" — amber dashed border + "Interno" badge
 *   message.menuData present — delegates to MenuCard (rich interaction preview)
 */

import React from "react";
import { AuthorType, ChatMessage } from "../types";
import { MenuCard } from "./MenuCard";

interface MessageBubbleProps {
  message: ChatMessage;
}

const AUTHOR_LABELS: Record<AuthorType, string> = {
  customer: "Cliente",
  agent_human: "Você",
  agent_ai: "IA",
  system: "Sistema",
};

const BUBBLE_STYLES: Record<AuthorType, string> = {
  customer: "bg-gray-100 text-gray-800 self-start rounded-tl-none",
  agent_human: "bg-indigo-600 text-white self-end rounded-tr-none",
  agent_ai: "bg-violet-100 text-violet-900 self-start rounded-tl-none",
  system: "bg-amber-50 text-amber-800 self-center text-xs italic border border-amber-200",
};

// agents_only overrides: amber tinted background + dashed border, positioned left
const INTERNAL_BUBBLE =
  "bg-amber-50 text-amber-900 self-start rounded-tl-none border border-dashed border-amber-400";

const LABEL_STYLES: Record<AuthorType, string> = {
  customer: "text-left text-gray-500",
  agent_human: "text-right text-indigo-400",
  agent_ai: "text-left text-violet-500",
  system: "text-center text-amber-600",
};

const INTERNAL_LABEL = "text-left text-amber-600";

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

export const MessageBubble: React.FC<MessageBubbleProps> = ({ message }) => {
  // Menu cards are rendered as a standalone rich component, not a chat bubble.
  if (message.menuData) {
    return <MenuCard data={message.menuData} />;
  }

  const isInternal = message.visibility === "agents_only";
  const isRight = !isInternal && message.author === "agent_human";

  const labelStyle = isInternal
    ? INTERNAL_LABEL
    : LABEL_STYLES[message.author] ?? "text-left text-gray-500";

  const bubbleStyle = isInternal
    ? INTERNAL_BUBBLE
    : BUBBLE_STYLES[message.author] ?? "bg-gray-100 text-gray-800 self-start";

  return (
    <div
      className={`flex flex-col max-w-[80%] gap-0.5 ${
        isRight ? "self-end items-end" : "self-start items-start"
      }`}
    >
      <span className={`text-[10px] px-1 ${labelStyle}`}>
        {isInternal && (
          <span className="inline-flex items-center bg-amber-200 text-amber-800 rounded px-1 py-0 text-[9px] font-semibold mr-1 leading-tight">
            Interno
          </span>
        )}
        {AUTHOR_LABELS[message.author] ?? message.author} · {formatTime(message.timestamp)}
      </span>
      <div className={`px-3 py-2 rounded-2xl text-sm leading-relaxed ${bubbleStyle}`}>
        {message.text}
      </div>
    </div>
  );
};
