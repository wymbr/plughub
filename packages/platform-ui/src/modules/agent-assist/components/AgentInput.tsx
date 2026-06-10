/**
 * AgentInput
 * Text composition area. Contains only:
 *   [/ (canned)]  [textarea]  [Enviar]
 *
 * "Encerrar" was moved to ActionBar.
 * The "/" button opens CannedPhrasesPalette above the input.
 */

import React, { KeyboardEvent, useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Clock } from "lucide-react";
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
  const { t } = useTranslation('agentAssist');
  const inputDisabled = disabled;
  const [text,         setText]         = useState("");
  const [showPalette,  setShowPalette]  = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Re-focus textarea when palette closes
  const closePalette = useCallback(() => {
    setShowPalette(false);
    requestAnimationFrame(() => textareaRef.current?.focus({ preventScroll: true }));
  }, []);

  // Insert phrase or @mention from palette
  const handlePhraseSelect = useCallback((text: string) => {
    setText(text);
    closePalette();
  }, [closePalette]);

  // Keep textarea focused when focus leaves to document.body — but only if
  // focus is not moving to another interactive element inside this component
  // or to an overlay (palette, modal).  This avoids creating a focus trap
  // for keyboard users navigating away intentionally.
  const handleBlur = useCallback(
    (e: React.FocusEvent<HTMLTextAreaElement>) => {
      if (inputDisabled || showPalette) return;
      const rel = e.relatedTarget as Element | null;
      // Only re-focus if focus truly left the page (rel is null) and no
      // other element within the agent-input area was clicked.
      if (!rel && !showPalette) {
        // Do NOT re-focus — let keyboard users navigate freely.
        // The textarea can be re-activated by clicking or pressing Tab back.
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
        ? "border-warning/30 bg-warning-light"
        : "border-border bg-white"
    }`}>
      {/* Wrap-up banner — input remains active so the agent can respond to hook agents */}
      {sessionClosed && (
        <p className="flex items-center justify-center gap-1.5 text-xs text-warning-text text-center leading-snug mb-2">
          <Clock className="w-3.5 h-3.5 flex-shrink-0" aria-hidden="true" />
          {t('agentInput.wrapUpBanner')}
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
          aria-label={t('agentInput.cannedAriaLabel')}
          aria-expanded={showPalette}
          aria-controls="canned-palette"
          className={`flex-shrink-0 w-11 h-11 rounded-lg border text-sm font-mono font-semibold
            flex items-center justify-center transition-colors self-end
            disabled:opacity-40 disabled:cursor-not-allowed
            ${showPalette
              ? "bg-primary border-primary text-white"
              : "bg-surface border-border text-muted hover:border-primary hover:text-primary"
            }`}
        >
          <span aria-hidden="true">/</span>
        </button>

        {/* Textarea */}
        <label htmlFor="agent-message-input" className="sr-only">
          {t('agentInput.label')}
        </label>
        <textarea
          ref={textareaRef}
          id="agent-message-input"
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
          disabled={inputDisabled}
          rows={2}
          placeholder={t('agentInput.placeholder')}
          aria-describedby="agent-input-hint"
          className="flex-1 resize-none rounded-lg border border-border px-3 py-2 text-sm
            focus:outline-none focus:ring-2 focus:ring-primary
            disabled:bg-surface-muted disabled:text-muted"
        />
        <p id="agent-input-hint" className="sr-only">
          {t('agentInput.hint')}
        </p>

        {/* Send button */}
        <button
          onClick={handleSend}
          disabled={inputDisabled || !text.trim()}
          aria-label={t('agentInput.sendAriaLabel')}
          className="px-4 py-2 h-11 rounded-lg bg-primary text-white text-sm font-medium
            hover:bg-primary-dark disabled:opacity-40 disabled:cursor-not-allowed
            transition-colors self-end"
        >
          {t('input.send')}
        </button>
      </div>
    </div>
  );
};
