/**
 * AgentInput
 * Text composition area. Contains only:
 *   [/ (canned)]  [textarea]  [Enviar]
 *
 * "Encerrar" was moved to ActionBar.
 * The "/" button opens CannedPhrasesPalette above the input.
 */

import React, { KeyboardEvent, useCallback, useRef, useState } from "react";
import { SupervisorCapabilities } from "../types";
import { CannedPhrasesPalette } from "./CannedPhrasesPalette";

interface AgentInputProps {
  onSend:        (text: string) => void;
  disabled?:     boolean;
  sessionClosed?: boolean;
  capabilities?: SupervisorCapabilities | null;
}

export const AgentInput: React.FC<AgentInputProps> = ({
  onSend,
  disabled      = false,
  sessionClosed = false,
  capabilities,
}) => {
  // During wrap-up (sessionClosed=true) the input stays active so the agent
  // can respond to hook agent prompts (wrap-up notes, classification, etc.).
  const inputDisabled = disabled;
  const [text,         setText]         = useState("");
  const [showPalette,  setShowPalette]  = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Re-focus textarea when palette closes
  const closePalette = useCallback(() => {
    setShowPalette(false);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, []);

  // Insert phrase or @mention from palette
  const handlePhraseSelect = useCallback((text: string) => {
    setText(text);
    closePalette();
  }, [closePalette]);

  // Keep textarea focused unless modal/palette is open
  const handleBlur = useCallback(
    (e: React.FocusEvent<HTMLTextAreaElement>) => {
      if (inputDisabled || showPalette) return;
      const rel = e.relatedTarget as Element | null;
      if (!rel) {
        requestAnimationFrame(() => {
          if (!inputDisabled && !showPalette &&
            (document.activeElement === document.body || document.activeElement === null)) {
            textareaRef.current?.focus();
          }
        });
      }
    },
    [inputDisabled, showPalette]
  );

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || inputDisabled) return;
    onSend(trimmed);
    setText("");
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends; Shift+Enter is a new line
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
      return;
    }
    // "/" at start of empty input opens palette
    if (e.key === "/" && !e.shiftKey && text === "") {
      e.preventDefault();
      setShowPalette(true);
    }
  };

  return (
    <div className={`border-t px-3 py-2 flex-shrink-0 relative ${
      sessionClosed
        ? "border-amber-200 bg-amber-50"
        : "border-gray-200 bg-white"
    }`}>
      {/* Wrap-up banner — input remains active so the agent can respond to hook agents */}
      {sessionClosed && (
        <p className="text-xs text-amber-700 text-center leading-snug mb-2">
          ⏳ Wrap-up em andamento — responda às perguntas dos agentes de finalização abaixo.
        </p>
      )}
      {/* Canned phrases palette — floats above the input */}
      {showPalette && (
        <CannedPhrasesPalette
          capabilities={capabilities}
          onSelect={handlePhraseSelect}
          onClose={closePalette}
        />
      )}

      <div className="flex items-end gap-2">
        {/* "/" canned phrases button */}
        <button
          onClick={() => setShowPalette(v => !v)}
          disabled={inputDisabled}
          title='Frases rápidas e @especialistas (ou pressione "/" no início da mensagem)'
          className={`flex-shrink-0 w-8 h-8 rounded-lg border text-sm font-mono font-semibold
            flex items-center justify-center transition-colors self-end mb-0.5
            disabled:opacity-40 disabled:cursor-not-allowed
            ${showPalette
              ? "bg-indigo-600 border-indigo-600 text-white"
              : "bg-white border-gray-200 text-gray-500 hover:border-indigo-400 hover:text-indigo-600"
            }`}
        >
          /
        </button>

        {/* Textarea */}
        <textarea
          ref={textareaRef}
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
          disabled={inputDisabled}
          rows={2}
          placeholder="Digite sua mensagem… (Enter envia · Shift+Enter nova linha · / para frases)"
          className="flex-1 resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm
            focus:outline-none focus:ring-2 focus:ring-indigo-500
            disabled:bg-gray-50 disabled:text-gray-400"
        />

        {/* Send button */}
        <button
          onClick={handleSend}
          disabled={inputDisabled || !text.trim()}
          className="px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium
            hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed
            transition-colors self-end"
        >
          Enviar
        </button>
      </div>
    </div>
  );
};
