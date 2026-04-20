/**
 * MessageBubble
 * Renders a single chat message with author-specific styling.
 */

import React from "react";
import { AuthorType, ChatMessage } from "../types";

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

const LABEL_STYLES: Record<AuthorType, string> = {
  customer: "text-left text-gray-500",
  agent_human: "text-right text-indigo-400",
  agent_ai: "text-left text-violet-500",
  system: "text-center text-amber-600",
};

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
  const isRight = message.author === "agent_human";

  return (
    <div
      className={`flex flex-col max-w-[80%] gap-0.5 ${
        isRight ? "self-end items-end" : "self-start items-start"
      }`}
    >
      <span className={`text-[10px] px-1 ${LABEL_STYLES[message.author]}`}>
        {AUTHOR_LABELS[message.author]} · {formatTime(message.timestamp)}
      </span>
      <div
        className={`px-3 py-2 rounded-2xl text-sm leading-relaxed ${BUBBLE_STYLES[message.author]}`}
      >
        {message.text}
      </div>
    </div>
  );
};
